#!/usr/bin/env python3
"""附錄 1 獨立驗收走查 —— 預覽跳段與後端 `_pad_and_merge_cuts` 併軌（吃 cut_pad）。

驗的是「瀏覽器實測數字」，不是 diff：真的 seek 到某個秒數、真的按播放，量播放頭最後
停在哪一秒。前端原本自帶一套沒有 pad 的合併規則，預覽每段都比成品短 cut_pad 秒／側；
併軌後預覽的跳段邊界必須跟 `assemble.cut_intervals_from_cfg` 算出來的完全一致。

沙盒集帶 subtitle_offset_sec: 1.5，所以「磁碟軸（yaml/srt）」與「顯示軸（影片播放頭）」
差 1.5 秒。期待值全部寫成顯示軸，並在第 4 項直接拿後端算出的磁碟軸區間 +1.5 對照，
偏移少減／多減一次都會紅。

三個階段（每階段改 episode.yaml 就重啟伺服器，Episode.cfg 只在建構時讀檔）：
  A. cut_pad=0.4 + UI 刪卡 #4  → 跳段邊界 [8.45, 10.05]（舊規則是 [8.85, 9.75]）
  B. cut_pad=0   + 同一張刪卡  → 邊界退回 [8.85, 9.75]（證明數字真的來自 cut_pad）
  C. cut_pad=0.4 + 只有 foreign cut（影片模式在卡間隙剪的）→ 預覽也要跳（舊版完全不跳）

每個 ✓ 都綁布林斷言（ok = 實得 == 期待），沒有只印值不斷言的行。

前提：headless Chrome CDP :9331（走查會自行起／收 serve_podcast.py）。
跑法：/usr/bin/python3 -u verify_cutpad_preview.py
"""
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp_common as C

HERE = Path(__file__).resolve().parent
SERVE = HERE / "serve_podcast.py"
REPO = HERE.parents[2]
POD = "http://127.0.0.1:8795/?pthook=1"
EPDIR = Path("/private/tmp/pt-timeline-baseline/episode/20260601 時間軸基準集")
YAML = EPDIR / "episode.yaml"
V2SRT = EPDIR / "03_成品" / "時間軸基準集_final_v2.srt"
OUT = HERE / "verify_cutpad_preview_result.json"

OFFSET = 1.5  # subtitle_offset_sec：顯示軸 = 磁碟軸 + OFFSET
PAD = 0.4
DEL_IDX = 4  # 磁碟 7.35–8.25 → 顯示 8.85–9.75；前鄰卡 #3 尾 8.15、後鄰卡 #5 頭 10.05
DEL_DISK = [7.35, 8.25]
# 影片模式在「卡 #4 尾 ~ 卡 #5 頭」的間隙剪的一段（磁碟軸），換不回任何一張卡
FOREIGN_DISK = [8.3, 8.45]

_server = None


def _port_free():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8795/api/episode", timeout=2):
            return False
    except Exception:
        return True


def stop_server():
    global _server
    if _server and _server.poll() is None:
        os.killpg(os.getpgid(_server.pid), signal.SIGTERM)
        _server.wait(timeout=10)
    _server = None
    if not _port_free():
        subprocess.run(["bash", "-c", "lsof -ti tcp:8795 | xargs -r kill"], check=False)
        for _ in range(20):
            if _port_free():
                break
            time.sleep(0.25)


def start_server():
    """起 serve_podcast.py 並等到 /api/episode 有回應。

    每改一次 yaml 都要重啟：Episode 在 __init__ 就把 episode.yaml 讀進 self.cfg，
    不重啟量到的是舊快照（cut_pad 會停在上一階段的值，整批數字看起來像功能壞掉）。
    """
    global _server
    stop_server()
    log = open("/private/tmp/pt-timeline-baseline/server-cutpad.log", "ab")
    _server = subprocess.Popen(
        [sys.executable, "-u", str(SERVE)],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(80):
        if not _port_free():
            return True
        time.sleep(0.25)
    return False


def read_yaml():
    import yaml as _y

    return _y.safe_load(YAML.read_text(encoding="utf-8")) or {}


def write_yaml(data):
    import yaml as _y

    YAML.write_text(
        _y.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def set_stage(cut_pad, cuts):
    """把 yaml 寫成該階段的起點並重啟伺服器。"""
    y = read_yaml()
    y["subtitle_offset_sec"] = OFFSET
    y["cut_pad"] = cut_pad
    y.pop("deletions", None)
    if cuts is None:
        y.pop("cuts", None)
    else:
        y["cuts"] = [list(c) for c in cuts]
    write_yaml(y)
    return start_server()


def backend_intervals(cfg=None):
    """後端（合成端）看到的剪除區間，磁碟軸。拿真的 _v2.srt 當卡表——傳空卡表會讓
    kept 為空、pad 一律不外擴，對照就失去意義。"""
    code = (
        "import sys, json, yaml;"
        f"sys.path.insert(0, {json.dumps(str(REPO))});"
        "from podcast_toolkit import srt_io;"
        "from podcast_toolkit.assemble import cut_intervals_from_cfg;"
        f"cfg = yaml.safe_load(open({json.dumps(str(YAML))}, encoding='utf-8'));"
        f"cards = srt_io.parse(open({json.dumps(str(V2SRT))}, encoding='utf-8').read());"
        "print(json.dumps([list(x) for x in cut_intervals_from_cfg(cfg, cards)]))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"__err": (r.stderr or "")[-400:]}


async def ws_url():
    with urllib.request.urlopen("http://127.0.0.1:9331/json/version", timeout=5) as r:
        return json.loads(r.read())["webSocketDebuggerUrl"]


async def wait_for(page, expr, timeout=25, interval=0.4):
    for _ in range(int(timeout / interval)):
        v = await C.js(page, expr)
        if v:
            return v
        await asyncio.sleep(interval)
    return None


async def open_pod(browser, ws, sessions):
    page = await C.open_page(browser, ws, sessions, POD, settle=2.0)
    ok = await wait_for(
        page, "document.querySelectorAll('#cards-list .card').length > 0"
    )
    return page, ok


def close(a, b, tol=0.06):
    return isinstance(a, (int, float)) and abs(float(a) - float(b)) <= tol


async def fingerprint(page):
    """來源未同步防呆：服務的 JS 必須是併軌後的版本，且 /api/episode 真的下放 cut_pad。"""
    return await C.js(
        page,
        """(async () => {
      const grab = async (p) => (await (await fetch(p, {cache:'no-store'})).text());
      const [core, app] = await Promise.all([
        grab('/static/timeline-core.js'), grab('/static/app.js'),
      ]);
      const ep = await (await fetch('/api/episode', {cache:'no-store'})).json();
      return {
        core: core.includes('export function padAndMergeCuts'),
        app: app.includes('padAndMergeCuts') && app.includes('state.cutPad'),
        old_gone: !app.includes('mergeDeletionIntervals'),
        cut_pad: ep.cut_pad,
      };
    })()""",
    )


async def media_ready(page):
    """含 seek 的驗證前置：readyState / seekable 都要成立，否則測到的是不可 seek 環境。"""
    return await C.js(
        page,
        """(() => {
      const v = document.querySelector('#video');
      if (!v) return null;
      return {ready: v.readyState, seekable: v.seekable.length, dur: v.duration};
    })()""",
    )


async def probe(page, t):
    """seek 到 t → 按播放 → 在 play 事件裡立刻暫停 → 回報播放頭停在哪。

    為什麼在 play 事件裡暫停：app.js 的 play handler 會同步把 currentTime 推到跳段
    末端，我們的 once 監聽在它之後執行，所以量到的是「跳完但幾乎沒播下去」的值，
    不受播放速度影響（時間漂移 < 一個 tick）。fired=false 代表 play 被瀏覽器擋掉，
    該筆量測無效（不可當成「沒跳」）。
    """
    return await C.js(
        page,
        """(async () => {
      const v = document.querySelector('#video');
      v.muted = true;
      v.pause();
      await new Promise((res) => {
        const done = () => { v.removeEventListener('seeked', done); res(); };
        v.addEventListener('seeked', done);
        v.currentTime = %r;
        setTimeout(done, 3000);
      });
      const before = v.currentTime;
      let fired = false;
      await new Promise((res) => {
        v.addEventListener('play', () => { fired = true; v.pause(); res(); }, {once:true});
        v.play().catch(() => res());
        setTimeout(res, 2000);
      });
      await new Promise((r) => setTimeout(r, 150));
      const after = v.currentTime;
      v.pause();
      return {before, after, fired};
    })()"""
        % float(t),
    )


async def check_probe(page, label, t, want, jump):
    """量一次並斷言。jump=True 表示期待跳到 want；False 表示期待原地（want=t）。"""
    p = await probe(page, t)
    ok = bool(p) and p.get("fired") and close(p.get("before"), t) and close(p.get("after"), want)
    C.check(label, ok, p, {"after": want, "fired": True})
    return p


async def red_keys(page):
    return await C.js(
        page,
        "[...document.querySelectorAll('#cards-list .card.deleted')]"
        ".map(e => e.dataset.idx).sort()",
    )


async def del_btn_center(page, idx):
    return await C.js(
        page,
        """(() => {
      const card = document.querySelector('#cards-list .card[data-idx="%d"]');
      if (!card) return null;
      const b = card.querySelector('.card-del');
      if (!b) return null;
      b.scrollIntoView({block:'center'});
      const r = b.getBoundingClientRect();
      return {cx: r.left + r.width/2, cy: r.top + r.height/2};
    })()"""
        % idx,
    )


async def do_save(page):
    """按儲存並確認真的存成功（按鈕回報已儲存 + yaml mtime 前進 + 無錯誤 toast）。"""
    before = YAML.stat().st_mtime_ns
    await C.js(page, "document.querySelector('#save-btn').click()")
    said = await wait_for(
        page,
        "/已儲存/.test(document.querySelector('#save-btn').textContent)",
        timeout=25,
    )
    moved = False
    for _ in range(50):
        if YAML.stat().st_mtime_ns != before:
            moved = True
            break
        await asyncio.sleep(0.3)
    err = await C.js(
        page,
        "[...document.querySelectorAll('.toast, .toast-error')]"
        ".map(e => e.textContent).filter(t => /失敗/.test(t))",
    )
    await asyncio.sleep(0.6)
    return bool(said) and moved and not err


async def main():
    # ══ 階段 A：cut_pad=0.4，從乾淨起點用 UI 刪卡 #4 ══
    if not set_stage(PAD, []):
        print("！伺服器起不來，中止", flush=True)
        return ["server"]

    url = await ws_url()
    browser, sessions, ws = await C.connect(url)
    page, ready = await open_pod(browser, ws, sessions)
    C.check("A0 開得起來：卡片已渲染", bool(ready), bool(ready), True)
    if not ready:
        return C.summary()

    fp = await fingerprint(page)
    ok_fp = (
        bool(fp)
        and fp.get("core")
        and fp.get("app")
        and fp.get("old_gone")
        and close(fp.get("cut_pad"), PAD, 1e-6)
    )
    C.check(
        "A1 來源已同步：JS 是併軌版、舊的 mergeDeletionIntervals 已移除、/api/episode 下放 cut_pad=0.4",
        ok_fp,
        fp,
        {"core": True, "app": True, "old_gone": True, "cut_pad": PAD},
    )
    if not ok_fp:
        print("！來源未同步，中止（不要把後面的紅當成功能壞掉）", flush=True)
        return C.summary()

    m = await media_ready(page)
    ok_m = bool(m) and m.get("ready", 0) >= 1 and m.get("seekable", 0) > 0
    C.check("A2 影片可 seek（readyState>=1 且 seekable.length>0）", ok_m, m, "ready>=1, seekable>0")
    if not ok_m:
        print("！環境不可 seek，後面的量測沒有意義，中止", flush=True)
        return C.summary()

    rect = await del_btn_center(page, DEL_IDX)
    C.check(f"A3a 卡 #{DEL_IDX} 的刪除鈕找得到", bool(rect), bool(rect), True)
    if rect:
        top = await C.top_element_at(page, rect["cx"], rect["cy"])
        cls = top.get("cls") if isinstance(top, dict) else None
        cls_s = cls if isinstance(cls, str) else ""
        hit = bool(top) and (
            "card-del" in cls_s or str(top.get("tag")) in ("svg", "path", "BUTTON")
        )
        C.check("A3b 該座標最上層就是刪除鈕（沒被疊層吃掉）", hit, top, "card-del / 其 svg 子節點")
        await C.mouse_click(page, rect["cx"], rect["cy"])
        await asyncio.sleep(0.5)
    reds = await red_keys(page)
    C.check("A3c 紅卡 = {4}", reds == ["4"], reds, ["4"])

    # 顯示軸期待值：卡 #4 = 8.85–9.75；左界夾在卡 #3 尾 8.15（8.85-0.4=8.45 > 8.15 → 8.45）、
    # 右界夾在卡 #5 頭 10.05（9.75+0.4=10.15 > 10.05 → 10.05）
    A_LO, A_HI = 8.45, 10.05
    await check_probe(page, f"A4a 區間左緣外（8.30）不跳——pad 沒有無限外擴", 8.30, 8.30, False)
    await check_probe(
        page, f"A4b 舊規則不會跳的 8.50（落在 pad 延伸出來的左段）→ 跳到 {A_HI}", 8.50, A_HI, True
    )
    await check_probe(
        page, f"A4c 舊規則停在原地的 9.85（落在 pad 延伸出來的右段）→ 跳到 {A_HI}", 9.85, A_HI, True
    )
    await check_probe(page, "A4d 保留卡 #5 內（10.20）不跳", 10.20, 10.20, False)

    ok_save = await do_save(page)
    C.check("A5a 存檔完成（按鈕回報已儲存 + yaml mtime 前進 + 無錯誤 toast）", ok_save, ok_save, True)
    y = read_yaml()
    got_cuts = y.get("cuts")
    ok_cuts = (
        isinstance(got_cuts, list)
        and len(got_cuts) == 1
        and close(got_cuts[0][0], DEL_DISK[0], 0.005)
        and close(got_cuts[0][1], DEL_DISK[1], 0.005)
    )
    C.check("A5b yaml 寫入磁碟軸 cuts", ok_cuts, got_cuts, [DEL_DISK])
    C.check(
        "A5c 存檔沒有吃掉 cut_pad（前端唯讀，round-trip 不掉值）",
        close(y.get("cut_pad"), PAD, 1e-6),
        y.get("cut_pad"),
        PAD,
    )

    iv = backend_intervals()
    ok_iv = (
        isinstance(iv, list)
        and len(iv) == 1
        and close(iv[0][0] + OFFSET, A_LO, 0.005)
        and close(iv[0][1] + OFFSET, A_HI, 0.005)
    )
    C.check(
        "A6 後端 cut_intervals_from_cfg 的區間 +1.5 = 瀏覽器量到的跳段邊界（預覽＝成品）",
        ok_iv,
        iv,
        [[A_LO - OFFSET, A_HI - OFFSET]],
    )

    # ══ 階段 B：cut_pad=0 —— 邊界必須退回原卡外緣（證明數字真的來自 cut_pad）══
    if not set_stage(0, [DEL_DISK]):
        C.check("B0 伺服器重啟（cut_pad=0）", False, "start_server 失敗", True)
        return C.summary()
    pageB, readyB = await open_pod(browser, ws, sessions)
    C.check("B0 cut_pad=0 的集開得起來", bool(readyB), bool(readyB), True)
    mB = await media_ready(pageB)
    C.check(
        "B1 影片可 seek",
        bool(mB) and mB.get("seekable", 0) > 0,
        mB,
        "seekable>0",
    )
    redsB = await red_keys(pageB)
    C.check("B2 cuts 換回紅卡 {4}", redsB == ["4"], redsB, ["4"])
    await check_probe(pageB, "B3a 8.50 不再跳（pad=0 → 左緣退回 8.85）", 8.50, 8.50, False)
    await check_probe(pageB, "B3b 9.00 跳到卡尾 9.75（不是 10.05）", 9.00, 9.75, True)

    # ══ 階段 C：只有 foreign cut（影片模式在卡間隙剪的）══
    if not set_stage(PAD, [FOREIGN_DISK]):
        C.check("C0 伺服器重啟（foreign cut）", False, "start_server 失敗", True)
        return C.summary()
    pageC, readyC = await open_pod(browser, ws, sessions)
    C.check("C0 帶 foreign cut 的集開得起來", bool(readyC), bool(readyC), True)
    mC = await media_ready(pageC)
    C.check("C1 影片可 seek", bool(mC) and mC.get("seekable", 0) > 0, mC, "seekable>0")
    redsC = await red_keys(pageC)
    C.check("C2 沒有任何紅卡（foreign cut 不擴寬成整張卡）", redsC == [], redsC, [])
    # 顯示軸 9.8–9.95，左界夾在卡 #4 尾 9.75、右界夾在卡 #5 頭 10.05
    C_HI = 10.05
    await check_probe(pageC, f"C3a 9.85 落在 foreign cut 內 → 跳到 {C_HI}（舊版完全不跳）", 9.85, C_HI, True)
    await check_probe(pageC, "C3b 保留卡 #4 內（9.60）不跳", 9.60, 9.60, False)
    ivC = backend_intervals()
    ok_ivC = (
        isinstance(ivC, list)
        and len(ivC) == 1
        and close(ivC[0][1] + OFFSET, C_HI, 0.005)
    )
    C.check(
        "C4 後端算出的區間末端 +1.5 = 瀏覽器量到的跳段落點",
        ok_ivC,
        ivC,
        [[9.75 - OFFSET, C_HI - OFFSET]],
    )

    errs = []
    for p in (page, pageB, pageC):
        errs += [e for e in C.console_errors(p) if "favicon" not in str(e)]
    C.check("D 全程無 console 例外", not errs, errs[:3], [])

    bad = C.summary()
    OUT.write_text(
        json.dumps(
            {"results": C.results_as_dict(), "failed": bad, "yaml_final": read_yaml()},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\n結果寫入 {OUT}", flush=True)
    await ws.close()
    return bad


if __name__ == "__main__":
    try:
        bad = asyncio.run(main())
    finally:
        stop_server()
    sys.exit(1 if bad else 0)
