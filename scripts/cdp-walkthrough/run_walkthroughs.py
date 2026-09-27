#!/usr/bin/env python3
"""CDP 走查總跑器：一支指令從零把環境架起來、依序跑完走查、回傳可當關卡的 exit code。

為什麼需要它：timeline-baseline/ 的走查各自會起 serve_podcast.py，但都假設「外面已經有
一個 headless Chrome 掛在 CDP_PORT 上」、「沙盒集已經建好」。以前這兩件事是我手動做的，
所以這些護欄只跑在我這台機器、CI 一條都跑不到（TODO.md 2026-09-26 記的缺口）。
這支把「建沙盒 → 起 Chrome（全新 profile）→ 逐支跑 → 收攤」串成一條命令。

跑法：
  /usr/bin/python3 -u scripts/cdp-walkthrough/run_walkthroughs.py            # 跑預設清單
  ... run_walkthroughs.py --list                                             # 只列清單
  ... run_walkthroughs.py --only verify_toast_no_block                       # 只跑一支（可重複給）
  ... run_walkthroughs.py --include-fingerprint                              # 額外跑指紋護欄（見下）
需要：Python 3.9+、websockets（pip install -e ".[cdp]"）、ffmpeg、Google Chrome／Chromium。

「綠」的判準（三條同時成立才算過，只看 exit code 不夠）：
  1. 子程序 exit code == 0
  2. 它寫出的 {stem}_result.json 這次真的有重寫（跑前先把舊檔搬走，跑後必須存在）
  3. json 裡 status=="fail" 為 0 筆，且 status=="pass" 至少 1 筆
第 2、3 條是防「假綠」：走查裸跑 asyncio.run() 不傳 exit code、或前提不成立整批 skip
（skip 不計入分母）時，光看 exit code 會綠得很漂亮。
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE / "timeline-baseline"
REPO = HERE.parent.parent
SANDBOX_ROOT = Path("/private/tmp/pt-timeline-baseline")
GRAVEYARD = SANDBOX_ROOT / "_graveyard"

# 預設清單：只收「自己會起 serve_podcast、且用 CDP_PORT 環境變數（沒寫死 Chrome port）」的走查。
# 其餘（verify_b.py／verify_b1_cuts.py／verify_cardtrack_toggle.py／verify_cutpad_preview.py／
# verify_b3_guard_and_shift.py）把 CDP port 寫死 9331，要進這裡得先改成吃 CDP_PORT —— 留待後續。
DEFAULT_SET = [
    ("verify_render_skeleton_loading.py", "loading 態骨架卡＋render.js 模組圖解得開"),
    ("verify_toast_no_block.py", "toast 不擋點擊"),
    ("verify_offset_badinput.py", "偏移欄位壞輸入不靜默清零"),
    ("verify_nextkeeptime_verdict.py", "nextKeepTime 保留卡守衛"),
    ("verify_podcast_caption_style.py", "podcast 字幕樣式預覽"),
    ("verify_caption_style_panel.py", "字幕樣式面板 8 參數＋存檔 round-trip"),
    ("verify_editor_small_defects.py", "F 梯三項（快捷鍵表／A-B tooltip／操作欄寬）"),
]
# 指紋護欄：baseline 是在我這台機器錄的（outerHTML 的 sha），跨機器只要有一個字不同就全紅。
# 它的價值在「同一台機器上動刀前後零變更」，不適合當 CI 的預設關卡，所以要 --include-fingerprint
# 才跑。CI 想用它得先確認 baseline 在 runner 上重現得出來（目前未確認，不要假設）。
FINGERPRINT = ("verify_render_fingerprint.py", "渲染指紋護欄（baseline 綁機器，預設不跑）")

CDP_PORT = int(os.environ.get("CDP_PORT", "9522"))
POD_PORT = 8795
PER_SCRIPT_TIMEOUT = int(os.environ.get("WALKTHROUGH_TIMEOUT", "900"))


def retire(path: Path) -> None:
    """把檔案／目錄搬進 _graveyard（不用 rm -rf、不用 shutil.rmtree）。"""
    if not path.exists():
        return
    GRAVEYARD.mkdir(parents=True, exist_ok=True)
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    shutil.move(str(path), str(GRAVEYARD / f"{path.name}-{stamp}"))


def chrome_path() -> str:
    """挑一顆 Chrome／Chromium：環境變數 CHROME > macOS app bundle > PATH。"""
    env = os.environ.get("CHROME")
    if env:
        return env
    cands = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    ]
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        w = shutil.which(name)
        if w:
            cands.append(w)
    for c in cands:
        if Path(c).exists():
            return c
    sys.exit("找不到 Chrome／Chromium；可用 CHROME=/path/to/chrome 指定")


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def free_port(port: int) -> None:
    """收掉別人留在該 port 的程序（走查要能重跑；serve_podcast 沒寫 lockfile）。"""
    if not port_open(port):
        return
    subprocess.run(["bash", "-c", f"lsof -ti tcp:{port} | xargs kill"], check=False)
    for _ in range(20):
        if not port_open(port):
            return
        time.sleep(0.25)
    print(f"! {port} 還被占著，走查可能會失敗", flush=True)


def build_sandbox() -> None:
    print("── 建沙盒集 ──", flush=True)
    r = subprocess.run([sys.executable, str(BASE / "build_sandbox_episode.py")], text=True)
    if r.returncode != 0:
        sys.exit("建沙盒集失敗（見上方輸出）")


def start_chrome(port: int):
    """起 headless Chrome，profile 目錄每次全新。

    為什麼一定要全新 profile：舊 user-data-dir 的媒體快取毀損過一次，症狀是影片永遠
    載不完（readyState=0）卻不拋任何錯，整批走查看起來像產品壞了。換新 profile 就好。
    """
    exe = chrome_path()
    profile = Path(f"/private/tmp/pt-cdp-profile-{os.getpid()}")
    retire(profile)
    profile.mkdir(parents=True, exist_ok=True)
    log = open(profile / "chrome.log", "ab")
    p = subprocess.Popen(
        [
            exe,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--mute-audio",
            "--hide-scrollbars",
            "--autoplay-policy=no-user-gesture-required",
            "about:blank",
        ],
        stdout=log,
        stderr=log,
    )
    for _ in range(80):
        if p.poll() is not None:
            sys.exit(f"Chrome 啟動即退出，看 {profile / 'chrome.log'}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                ver = json.loads(r.read())["Browser"]
            print(f"Chrome 就緒：{ver}  CDP=:{port}  profile={profile}", flush=True)
            return p, profile
        except (urllib.error.URLError, socket.timeout, OSError):
            time.sleep(0.25)
    p.kill()
    sys.exit(f"Chrome 20 秒內沒開 CDP :{port}，看 {profile / 'chrome.log'}")


def stop_chrome(p, profile: Path) -> None:
    if p is not None and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=5)
    retire(profile)


def extract_rows(payload):
    """從結果檔挖出斷言列。

    走查的結果檔有兩種形狀（歷史演進，不強求統一）：
      1. 直接是 results_as_dict() 的 list
      2. dict，斷言列在 "results"（另外掛 mode／failed／yaml_final 之類的附註）
    只認「每列都是 dict 且有 status」的那個 list；認不出來就拋例外，讓這支走查判紅 ——
    看不懂結果檔不可以當成綠。
    """
    cand = payload
    if isinstance(payload, dict):
        cand = payload.get("results")
    if not isinstance(cand, list):
        raise ValueError(f"找不到斷言列（頂層是 {type(payload).__name__}）")
    bad = [r for r in cand if not (isinstance(r, dict) and "status" in r)]
    if bad:
        raise ValueError(f"{len(bad)} 列不是斷言列，例：{str(bad[0])[:80]}")
    return cand


def verdict(script: Path, code: int, timed_out: bool):
    """回傳 (ok, 說明)。三條判準見模組 docstring。"""
    if timed_out:
        return False, f"逾時 {PER_SCRIPT_TIMEOUT}s 被中止"
    rj = BASE / f"{script.stem}_result.json"
    if not rj.exists():
        return False, f"沒寫出 {rj.name}（可能開場就炸）"
    try:
        rows = extract_rows(json.loads(rj.read_text(encoding="utf-8")))
    except Exception as e:
        return False, f"{rj.name} 讀不動：{e}"
    fails = [r["name"] for r in rows if r.get("status") == "fail"]
    passes = [r for r in rows if r.get("status") == "pass"]
    skips = [r for r in rows if r.get("status") == "skip"]
    tail = f"（{len(skips)} 項 skip）" if skips else ""
    if fails:
        return False, f"{len(fails)} 項紅：{'、'.join(fails[:4])}{'…' if len(fails) > 4 else ''}"
    if not passes:
        return False, f"一項 pass 都沒有{tail} —— 前提沒成立，這不是綠"
    if code != 0:
        return False, f"斷言全綠但 exit code={code}（收攤階段出事）"
    return True, f"{len(passes)} 項綠{tail}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[], help="只跑這幾支（檔名可省 .py，可重複）")
    ap.add_argument("--include-fingerprint", action="store_true", help="額外跑指紋護欄")
    ap.add_argument("--list", action="store_true", help="列出清單就結束")
    ap.add_argument("--cdp-port", type=int, default=CDP_PORT)
    args = ap.parse_args()

    plan = list(DEFAULT_SET) + ([FINGERPRINT] if args.include_fingerprint else [])
    if args.only:
        want = {o[:-3] if o.endswith(".py") else o for o in args.only}
        pool = dict((s, n) for s, n in list(DEFAULT_SET) + [FINGERPRINT])
        plan = [(s, n) for s, n in pool.items() if Path(s).stem in want]
        missing = want - {Path(s).stem for s, _ in plan}
        if missing:
            sys.exit(f"--only 指到不存在的走查：{'、'.join(sorted(missing))}")

    if args.list:
        for s, n in list(DEFAULT_SET) + [FINGERPRINT]:
            mark = "預設" if (s, n) in DEFAULT_SET else "選用"
            print(f"[{mark}] {s:42s} {n}")
        return 0

    t0 = time.time()
    build_sandbox()
    free_port(POD_PORT)
    chrome, profile = start_chrome(args.cdp_port)
    rows = []
    try:
        for script, note in plan:
            path = BASE / script
            if not path.exists():
                rows.append((script, False, "檔案不存在", 0.0))
                continue
            retire(BASE / f"{path.stem}_result.json")  # 舊結果先搬走，免得被當成這次的綠
            print("\n" + "=" * 70, flush=True)
            print(f"▶ {script}　{note}", flush=True)
            print("=" * 70, flush=True)
            env = dict(os.environ, CDP_PORT=str(args.cdp_port), PYTHONUNBUFFERED="1")
            s0 = time.time()
            timed_out = False
            try:
                r = subprocess.run(
                    [sys.executable, "-u", str(path)],
                    cwd=str(BASE), env=env, timeout=PER_SCRIPT_TIMEOUT,
                )
                code = r.returncode
            except subprocess.TimeoutExpired:
                timed_out, code = True, -1
            dt = time.time() - s0
            free_port(POD_PORT)  # 走查自己收攤失敗時，別把 8795 留給下一支
            ok, why = verdict(path, code, timed_out)
            rows.append((script, ok, why, dt))
            print(f"{'✓' if ok else '✗'} {script}：{why}（{dt:.0f}s）", flush=True)
    finally:
        stop_chrome(chrome, profile)

    print("\n" + "#" * 70, flush=True)
    print(f"CDP 走查總結（{time.time() - t0:.0f}s）", flush=True)
    for script, ok, why, dt in rows:
        print(f"  {'✓' if ok else '✗'} {script:42s} {why}  [{dt:.0f}s]", flush=True)
    bad = [s for s, ok, _, _ in rows if not ok]
    print(f"{len(rows) - len(bad)}/{len(rows)} 支走查通過", flush=True)
    if bad:
        print("失敗：" + "、".join(bad), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
