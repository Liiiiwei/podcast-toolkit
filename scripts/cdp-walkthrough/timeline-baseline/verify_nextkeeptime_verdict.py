#!/usr/bin/env python3
"""`nextKeepTime` 保留卡守衛裁決走查（B3 附錄第 2 項）。

背景：2026-09-25 的夾制上線後，「整個守衛拿掉」這個突變變成 0 紅 —— 守衛看起來只是冗餘。
但「摸不到」與「拿掉是對的」是兩件事，所以先找出它**唯一還摸得到**的情境再裁決：

  padAndMergeCuts 的夾制只讓位給「落在區間內」的卡界（保留卡的尾在裡面 → 左緣讓位；
  頭在裡面 → 右緣讓位）。若保留卡**整個包住**被刪卡（cs <= s 且 ce >= e），兩個條件都
  不成立 → 不夾，剪除區間原封不動地躺在保留卡語音裡。這是逐字時間戳把長卡起點往前推時
  真的會出現的形狀。

裁決：在這個情境裡守衛是**有害的**，不是保險。合成端（assemble）沒有對應守衛、照剪；
守衛卻讓預覽照播那一段 —— 預覽與成品對不上，正是守衛當初要解決的那類問題本身。
一個行為只留一個地方管：夾制是正典，`nextKeepTime` 只做區間查表。本走查釘住這個裁決。

場景（沙盒 _v2.srt 改一行 + yaml 刪一張卡）：
  卡 #4 = 磁碟 7.350–8.250（刪掉）；卡 #5 的起點從 8.550 往前挪到 **7.200**
  → 卡 #5 = [7.200, 10.150] 整個包住卡 #4。subtitle_offset_sec=1.5 → 顯示 = 磁碟 + 1.5。
  後端剪除區間 = [[7.35, 8.25]]（夾制沒讓位、pad 被卡 #5 的語音邊界夾死兩側）。
  預覽在顯示 9.30（磁碟 7.80，在卡 #5 語音裡也在剪除區間裡）必須**跳**到顯示 9.75。

突變（MUT-G）：把舊守衛原樣加回去 → N3 必須紅（停在 9.30，與後端剪掉的事實對不上）。

每個 ✓ 都綁布林斷言。走查自行起／收 serve_podcast.py，finally 還原 yaml 與 _v2.srt。
前提：headless Chrome CDP（預設 :9522，用 CDP_PORT 覆寫）。
跑法：CDP_PORT=9522 /usr/bin/python3 -u verify_nextkeeptime_verdict.py
"""
import asyncio
import json
import os
import re
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
APP_JS = REPO / "podcast_toolkit" / "web" / "static" / "app.js"
EPDIR = Path("/private/tmp/pt-timeline-baseline/episode/20260601 時間軸基準集")
YAML = EPDIR / "episode.yaml"
V2SRT = EPDIR / "03_成品" / "時間軸基準集_final_v2.srt"
OUT = HERE / "verify_nextkeeptime_verdict_result.json"
CDP_PORT = int(os.environ.get("CDP_PORT", "9522"))
POD = "http://127.0.0.1:8795/?pthook=1"

OFFSET = 1.5  # 顯示軸 = 磁碟軸 + 1.5
PAD = 0.4
CARD5_NEW = "00:00:07,200 --> 00:00:10,150"  # 卡 #5 起點前挪，整個包住卡 #4
CUT_DISK = [7.35, 8.25]  # 卡 #4 的原區間；夾制不讓位、pad 兩側被夾死 → 後端就是這一段
DISP_IN = 9.30  # 磁碟 7.80：在卡 #5 語音裡，也在剪除區間裡
DISP_OUT = 9.75  # 磁碟 8.25：剪除區間末端
DISP_EDGE = 8.90  # 磁碟 7.40：剛進剪除區間
DISP_KEEP = 10.00  # 磁碟 8.50：卡 #5 語音裡、剪除區間外 → 不該跳

_YAML_ORIG = YAML.read_text(encoding="utf-8")
_SRT_ORIG = V2SRT.read_text(encoding="utf-8")
_APP_ORIG = APP_JS.read_text(encoding="utf-8")

# MUT-G：把 2026-09-25 之前的守衛原樣加回去（含 foreign cut 的 B3-1 例外）
MUT_G_OLD = """function nextKeepTime(t) {
  for (const [s, e] of deletionIntervals()) {"""
MUT_G_NEW = """function nextKeepTime(t) {
  const inForeignCut = state.foreignCuts.some(([s, e]) => t >= s && t < e);
  if (!inForeignCut) {
    for (const r of expandedCards()) {
      if (!state.deletions.has(r.key) && t >= r.start && t < r.end) return t;
    }
  }
  for (const [s, e] of deletionIntervals()) {"""

_server = None


def port_free():
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
    if not port_free():
        subprocess.run(["bash", "-c", "lsof -ti tcp:8795 | xargs -r kill"], check=False)
        for _ in range(20):
            if port_free():
                break
            time.sleep(0.25)


def start_server():
    stop_server()
    global _server
    log = open("/private/tmp/pt-timeline-baseline/server-nkt.log", "ab")
    _server = subprocess.Popen(
        [sys.executable, "-u", str(SERVE)],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(80):
        if not port_free():
            return True
        time.sleep(0.25)
    return False


def seed():
    """卡 #5 起點前挪成包住卡 #4；yaml 刪掉卡 #4，offset 1.5、cut_pad 0.4。"""
    import yaml as _y

    blocks = re.split(r"\n\s*\n", _SRT_ORIG.strip())
    out = []
    hit = 0
    for b in blocks:
        lines = [x for x in b.splitlines() if x.strip()]
        if len(lines) >= 2 and lines[0].strip() == "5":
            lines[1] = CARD5_NEW
            hit += 1
        out.append("\n".join(lines))
    if hit != 1:
        raise SystemExit(f"seed: 卡 #5 命中 {hit} 次（預期 1）")
    V2SRT.write_text("\n\n".join(out) + "\n", encoding="utf-8")

    y = _y.safe_load(_YAML_ORIG) or {}
    y["subtitle_offset_sec"] = OFFSET
    y["cut_pad"] = PAD
    y["deletions"] = [4]
    y.pop("cuts", None)
    YAML.write_text(
        _y.safe_dump(y, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def backend_intervals():
    """合成端看到的剪除區間（磁碟軸）。拿真的 _v2.srt 當卡表。"""
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


def patch_app(old, new):
    t = APP_JS.read_text(encoding="utf-8")
    if t.count(old) != 1:
        raise SystemExit(f"patch_app: anchor 命中 {t.count(old)} 次（預期 1）")
    APP_JS.write_text(t.replace(old, new, 1), encoding="utf-8")


def restore_app():
    APP_JS.write_text(_APP_ORIG, encoding="utf-8")


async def ws_url():
    with urllib.request.urlopen(
        f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=5
    ) as r:
        return json.loads(r.read())["webSocketDebuggerUrl"]


async def wait_for(page, expr, timeout=25, interval=0.4):
    for _ in range(int(timeout / interval)):
        v = await C.js(page, expr)
        if v:
            return v
        await asyncio.sleep(interval)
    return None


async def close_page(browser, page):
    """關掉分頁（先停播，避免殘留分頁的影片繼續跑事件干擾下一頁）。"""
    try:
        await C.js(page, "document.querySelector('#video').pause()")
        await page.send("Page.close")
    except Exception:
        pass
    await asyncio.sleep(0.5)


async def open_pod(browser, ws, sessions):
    page = await C.open_page(browser, ws, sessions, POD, settle=2.0)
    ok = await wait_for(
        page, "document.querySelectorAll('#cards-list .card').length > 0"
    )
    return page, ok


def close(a, b, tol=0.06):
    return isinstance(a, (int, float)) and abs(float(a) - float(b)) <= tol


async def card_time_text(page, idx):
    """卡面時間欄原文（顯示軸）："#5\n0:08.70\n0:11.65"。"""
    return await C.js(
        page,
        "(() => { const e = document.querySelector("
        f"'#cards-list .card[data-idx=\"{idx}\"] .card-time-val'"
        "); return e ? e.textContent : null; })()",
    )


def parse_card_time(text):
    """把卡面時間欄解析成 (start, end) 秒數；解析不出來回 None（不吞錯當 0）。"""
    if not text:
        return None
    got = re.findall(r"(\d+):(\d+(?:\.\d+)?)", text)
    if len(got) < 2:
        return None
    return tuple(int(m) * 60 + float(s) for m, s in got[:2])


async def probe(page, t):
    """seek 到 t → 按播放 → 在 play 事件裡立刻暫停 → 回報播放頭停在哪。

    fired=false 代表 play 被瀏覽器擋掉，該筆量測無效（不可當成「沒跳」）。
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


async def check_probe(page, label, t, want):
    p = await probe(page, t)
    ok = (
        bool(p)
        and p.get("fired")
        and close(p.get("before"), t)
        and close(p.get("after"), want)
    )
    C.check(label, ok, p, {"after": want, "fired": True})
    return p


async def main():
    print("=" * 60, flush=True)
    print("nextKeepTime 保留卡守衛裁決走查", flush=True)
    print("=" * 60, flush=True)

    seed()
    if not start_server():
        C.check("V0.0 serve_podcast 起得來", False, None, "8795 有回應")
        C.summary()
        return

    ws = None
    browser = None
    try:
        cdp = await ws_url()
        browser, sessions, ws = await C.connect(cdp)

        page, loaded = await open_pod(browser, ws, sessions)
        C.check("V0.0 頁面載入、卡片渲染完成", bool(loaded), loaded, True)

        # 來源未同步防呆：服務的 app.js 必須是「守衛已拿掉」的版本
        fp = await C.js(
            page,
            """(async () => {
          const app = await (await fetch('/static/app.js', {cache:'no-store'})).text();
          return {
            guard_gone: !app.includes('if (!state.deletions.has(r.key) && t >= r.start'),
            has_fn: app.includes('function nextKeepTime(t)'),
          };
        })()""",
        )
        C.check(
            "V0.1 原始碼指紋：nextKeepTime 還在、舊守衛已不在",
            bool(fp) and fp.get("guard_gone") and fp.get("has_fn"),
            fp,
            {"guard_gone": True, "has_fn": True},
        )

        media = await C.js(
            page,
            "(() => { const v = document.querySelector('#video');"
            " return v ? {ready: v.readyState, seekable: v.seekable.length,"
            " dur: v.duration} : null; })()",
        )
        C.check(
            "V0.2 影片可 seek（readyState>=1 且 seekable>0，否則量到的是不可 seek 環境）",
            bool(media) and media.get("ready", 0) >= 1 and media.get("seekable", 0) > 0,
            media,
            {"ready": ">=1", "seekable": ">0"},
        )

        cards = await C.js(
            page,
            "(() => ({n: document.querySelectorAll('#cards-list .card').length,"
            " del: [...document.querySelectorAll('#cards-list .card.deleted')]"
            ".map(e => e.dataset.idx).sort()}))()",
        )
        C.check(
            "V0.3 場景就緒：11 張卡、只有卡 #4 標記刪除",
            bool(cards) and cards.get("n") == 11 and cards.get("del") == ["4"],
            cards,
            {"n": 11, "del": ["4"]},
        )

        # --- N1/N2：證明這是守衛唯一還摸得到的形狀 ---
        iv = backend_intervals()
        ok_iv = (
            isinstance(iv, list)
            and len(iv) == 1
            and close(iv[0][0], CUT_DISK[0], 0.005)
            and close(iv[0][1], CUT_DISK[1], 0.005)
        )
        C.check(
            "N1 後端剪除區間 = [[7.35, 8.25]]（保留卡整個包住 → 夾制兩個條件都不成立、"
            "不讓位；pad 被卡 #5 的語音邊界夾死兩側）",
            ok_iv,
            iv,
            [CUT_DISK],
        )

        # 卡面時間欄是顯示軸（磁碟 + subtitle_offset_sec）："#5\n0:08.70\n0:11.65"
        raw5 = await card_time_text(page, 5)
        c5 = parse_card_time(raw5)
        ok_contain = (
            bool(c5)
            and ok_iv
            and c5[0] <= CUT_DISK[0] + OFFSET + 1e-3
            and c5[1] >= CUT_DISK[1] + OFFSET - 1e-3
        )
        C.check(
            "N2 保留卡 #5 的語音整個包住那段剪除區間（顯示軸 8.70 <= 8.85 且 11.65 >= 9.75）"
            "—— 舊守衛唯一還摸得到的情境",
            ok_contain,
            {"raw": raw5, "parsed": c5},
            {"start": "<= 8.85", "end": ">= 9.75"},
        )

        # --- N3～N5：預覽行為必須跟後端一致 ---
        await check_probe(
            page,
            f"N3 顯示 {DISP_IN}（在保留卡 #5 語音裡、也在剪除區間裡）→ 跳到 {DISP_OUT}"
            "（舊守衛在的話會停在原地，與後端真的剪掉對不上）",
            DISP_IN,
            DISP_OUT,
        )
        await check_probe(
            page, f"N4 剛進區間的顯示 {DISP_EDGE} → 跳到 {DISP_OUT}", DISP_EDGE, DISP_OUT
        )
        await check_probe(
            page,
            f"N5 卡 #5 語音裡、剪除區間外的顯示 {DISP_KEEP} → 不跳"
            "（拿掉守衛沒有變成整段亂跳）",
            DISP_KEEP,
            DISP_KEEP,
        )

        errs = C.console_errors(page)
        C.check("N6 console 無未捕捉例外", not errs, errs, [])

        # --- MUT-G：把舊守衛加回去 → N3 必須紅 ---
        print("\n--- MUT-G 真突變：把 2026-09-25 之前的保留卡守衛加回去 ---", flush=True)
        await close_page(browser, page)
        patch_app(MUT_G_OLD, MUT_G_NEW)
        try:
            pr, loaded_r = await open_pod(browser, ws, sessions)
            red = await probe(pr, DISP_IN) if loaded_r else None
            await close_page(browser, pr)
        finally:
            restore_app()
        pg, loaded_g = await open_pod(browser, ws, sessions)
        green = await probe(pg, DISP_IN) if loaded_g else None

        red_stuck = bool(red) and red.get("fired") and close(red.get("after"), DISP_IN)
        green_jumps = (
            bool(green) and green.get("fired") and close(green.get("after"), DISP_OUT)
        )
        print(f"    [MUT-G-RED]   after={red and red.get('after')}", flush=True)
        print(f"    [MUT-G-GREEN] after={green and green.get('after')}", flush=True)
        C.check(
            "MUT-G-RED 守衛加回去 → 預覽停在原地（後端剪掉、預覽照播＝對不上）",
            red_stuck,
            red,
            {"after": DISP_IN},
        )
        C.check(
            "MUT-G-GREEN 還原後 → 預覽跳到區間末端（與後端一致）",
            green_jumps,
            green,
            {"after": DISP_OUT},
        )
        C.check(
            "MUT-G 真突變成立：守衛在這個情境不是保險而是製造分歧 —— 拿掉是對的，"
            "而且 N3 真的在測這件事",
            red_stuck and green_jumps,
            {"red": red and red.get("after"), "green": green and green.get("after")},
            {"red": DISP_IN, "green": DISP_OUT},
        )
        await close_page(browser, pg)
    finally:
        restore_app()
        stop_server()
        YAML.write_text(_YAML_ORIG, encoding="utf-8")
        V2SRT.write_text(_SRT_ORIG, encoding="utf-8")
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    C.summary()
    OUT.write_text(
        json.dumps(C.results_as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"→ {OUT}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
