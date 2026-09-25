#!/usr/bin/env python3
"""附錄 B3 獨立驗收走查 —— 預覽守門看來源（B3-1）＋ 軸位移 alignShift 併軌（B3-2）。

驗的是「瀏覽器實測數字」，不是 diff：真的 seek＋真的按播放量播放頭落點；真的點 ⏱ 鈕、
真的點「起點 −0.1s」、真的按儲存，再回頭讀 _v2.srt 的毫秒整數。

三個階段（每改一次 episode.yaml／_v2.srt 都要重啟伺服器，Episode.cfg 只在建構時讀檔）：

  A. B3-1：cut_pad=0.4，一段 foreign cut 塞在保留卡 #4 肚子裡（磁碟 7.6–7.9）。
     併軌前 `nextKeepTime` 的第一個迴圈是「t 落在未刪的保留卡內就直接回 t」，保留卡
     守門優先於 cut 區間 → 預覽整段播過去；改成看來源後必須跳到 cut 末端。

  B. 守衛回歸：把 _v2.srt 的卡 #5 起點挪到磁碟 7.5（與被刪的卡 #4 7.35–8.25 重疊，
     逐字時間戳常見的狀況）。此時「由卡換算出來的 cut」會覆蓋保留卡 #5 的頭 0.75 秒。
     守衛（t 落在未刪保留卡內就不跳）必須仍然護住 #5 的語音 —— 拿掉守衛就會紅。
     同時把「後端會剪掉、預覽刻意不跳」這條已知落差釘成數字（B5），任一側改了就紅。

  C. B3-2：subtitle_offset_sec=0.123 ＋ yaml 留著 audio.sync_offset=2.0 但沒接音檔。
     這是 `alignShift` 的 audioPath 守衛唯一會被行使的情境。載入端守衛壞掉 → 卡 #4
     顯示 0:05.47（C2 紅）；存檔端守衛壞掉 → SRT 寫成 00:00:09,247（C6 紅）；
     取整從 3 位小數退回 2 位 → SRT 寫成 00:00:07,250（C6 紅）。
     兩端都呼叫同一個 alignShift 之後，「守衛一起掉」是對稱的、SRT round-trip 抓不到，
     只有 C2 的顯示數字抓得到 —— 所以顯示斷言與毫秒斷言缺一不可。

每個 ✓ 都綁布林斷言（ok = 實得 == 期待），沒有只印值不斷言的行。
走查自行起／收 serve_podcast.py，並在 finally 還原 episode.yaml 與 _v2.srt。

前提：headless Chrome CDP :9331（要自己先開，見 MUTATIONS.md 跑法）。
跑法：/usr/bin/python3 -u verify_b3_guard_and_shift.py
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
POD = "http://127.0.0.1:8795/?pthook=1"
EPDIR = Path("/private/tmp/pt-timeline-baseline/episode/20260601 時間軸基準集")
YAML = EPDIR / "episode.yaml"
V2SRT = EPDIR / "03_成品" / "時間軸基準集_final_v2.srt"
OUT = HERE / "verify_b3_guard_and_shift_result.json"

OFFSET_AB = 1.5  # A／B 階段的 subtitle_offset_sec：顯示軸 = 磁碟軸 + 1.5
PAD = 0.4
# A 階段：塞在保留卡 #4（磁碟 7.35–8.25）肚子裡的 foreign cut
A_CUT_DISK = [7.6, 7.9]
A_LO, A_HI = 9.1, 9.4  # 顯示軸（pad 兩側都被卡 #4 自己的語音邊界夾死）
# B 階段：刪卡 #4，且卡 #5 起點被挪到磁碟 7.5（顯示 9.0）與它重疊
B_CUT_DISK = [7.35, 8.25]
B_LO, B_HI = 8.45, 9.75
B_OVERLAP = 0.75  # 後端剪除區間侵入保留卡 #5 的秒數（已知落差，待裁決）
# C 階段
C_OFFSET = 0.123
C_SYNC = 2.0
C_IDX = 4
C_DISP_S, C_DISP_E = "0:07.47", "0:08.37"  # 磁碟 7.350/8.250 + 0.123
C_AFTER_NUDGE = "0:07.37"
C_SRT_LINE = "00:00:07,247 --> 00:00:08,247"

_server = None
_YAML_ORIG = YAML.read_text(encoding="utf-8")
_SRT_ORIG = V2SRT.read_text(encoding="utf-8")


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
    global _server
    stop_server()
    log = open("/private/tmp/pt-timeline-baseline/server-b3.log", "ab")
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


def set_stage(*, offset, cut_pad, cuts, audio="__keep__"):
    """把 yaml 寫成該階段的起點並重啟伺服器。audio=None 代表把整個 audio 節點拿掉。"""
    y = read_yaml()
    y["subtitle_offset_sec"] = offset
    y["cut_pad"] = cut_pad
    y.pop("deletions", None)
    if cuts is None:
        y.pop("cuts", None)
    else:
        y["cuts"] = [list(c) for c in cuts]
    if audio != "__keep__":
        if audio is None:
            y.pop("audio", None)
        else:
            y["audio"] = audio
    write_yaml(y)
    return start_server()


def backend_intervals():
    """後端（合成端）看到的剪除區間，磁碟軸。拿真的 _v2.srt 當卡表。"""
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


def srt_card_line(idx):
    """回傳 _v2.srt 第 idx 張卡的時間行原文（對不到回 None）。"""
    blocks = re.split(r"\n\s*\n", V2SRT.read_text(encoding="utf-8").strip())
    for b in blocks:
        lines = [x for x in b.splitlines() if x.strip()]
        if len(lines) >= 2 and lines[0].strip() == str(idx):
            return lines[1].strip()
    return None


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
    """來源未同步防呆：服務的 JS 必須是 B3 之後的版本，且 /api/episode 真的下放 cut_pad。"""
    return await C.js(
        page,
        """(async () => {
      const grab = async (p) => (await (await fetch(p, {cache:'no-store'})).text());
      const [core, app, api, vproto] = await Promise.all([
        grab('/static/timeline-core.js'), grab('/static/app.js'),
        grab('/static/api.js'), grab('/static/video-edit-prototype.js'),
      ]);
      const ep = await (await fetch('/api/episode', {cache:'no-store'})).json();
      return {
        core_shift: core.includes('export function alignShift'),
        app_b31: app.includes('inForeignCut') && app.includes('alignShift(state)'),
        api_b32: api.includes('_diskOffset') && api.includes('alignShift'),
        vproto: vproto.includes('alignShift'),
        cut_pad: ep.cut_pad,
      };
    })()""",
    )


async def media_ready(page):
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


async def red_keys(page):
    return await C.js(
        page,
        "[...document.querySelectorAll('#cards-list .card.deleted')]"
        ".map(e => e.dataset.idx).sort()",
    )


async def card_time_text(page, idx):
    """卡面時間欄的原文（"#4\\n0:07.47\\n0:08.37"）。"""
    return await C.js(
        page,
        '(() => { const e = document.querySelector('
        f"'#cards-list .card[data-idx=\"{idx}\"] .card-time-val'"
        "); return e ? e.textContent : null; })()",
    )


async def te_inputs(page, idx):
    """⏱ 工具列兩個輸入框的值。"""
    return await C.js(
        page,
        '(() => { const bar = document.querySelector('
        f"'#cards-list .card[data-idx=\"{idx}\"] .card-time-edit'"
        "); if (!bar) return null;"
        " return [...bar.querySelectorAll('.te-input')].map(i => i.value); })()",
    )


async def el_center(page, selector):
    return await C.js(
        page,
        """(() => {
      const el = document.querySelector(%s);
      if (!el) return null;
      el.scrollIntoView({block:'center'});
      const r = el.getBoundingClientRect();
      return {cx: r.left + r.width/2, cy: r.top + r.height/2, w: r.width, h: r.height};
    })()"""
        % json.dumps(selector),
    )


async def click_el(page, selector):
    """真事件點某個選擇器，並斷言該座標最上層真的是它（疊層防呆）。回傳 (ok, top)。"""
    rect = await el_center(page, selector)
    if not rect or not rect.get("w"):
        return False, rect
    top = await C.top_element_at(page, rect["cx"], rect["cy"])
    await C.mouse_click(page, rect["cx"], rect["cy"])
    await asyncio.sleep(0.4)
    return True, top


async def do_save(page):
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
    extra = {}

    # ══ 階段 A（B3-1）：foreign cut 塞在保留卡 #4 肚子裡 ══
    if not set_stage(offset=OFFSET_AB, cut_pad=PAD, cuts=[A_CUT_DISK], audio=None):
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
        and fp.get("core_shift")
        and fp.get("app_b31")
        and fp.get("api_b32")
        and fp.get("vproto")
        and close(fp.get("cut_pad"), PAD, 1e-6)
    )
    C.check(
        "A1 來源已同步：core 有 alignShift、app 有 inForeignCut、api 走 _diskOffset、"
        "影片模式也接上，且 /api/episode 下放 cut_pad=0.4",
        ok_fp,
        fp,
        {"core_shift": True, "app_b31": True, "api_b32": True, "vproto": True, "cut_pad": PAD},
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

    reds = await red_keys(page)
    C.check("A3 沒有紅卡（卡內 foreign cut 不擴寬成整張卡）", reds == [], reds, [])

    iv = backend_intervals()
    ok_iv = (
        isinstance(iv, list)
        and len(iv) == 1
        and close(iv[0][0] + OFFSET_AB, A_LO, 0.005)
        and close(iv[0][1] + OFFSET_AB, A_HI, 0.005)
    )
    C.check(
        f"A4 後端 cut_intervals_from_cfg 的區間 +1.5 = [{A_LO}, {A_HI}]（pad 被卡 #4 自己的語音邊界夾死）",
        ok_iv,
        iv,
        [[A_LO - OFFSET_AB, A_HI - OFFSET_AB]],
    )

    await check_probe(
        page, f"A5a 保留卡 #4 內、落在 foreign cut 的 9.20 → 跳到 {A_HI}（B3-1 之前停在原地）", 9.20, A_HI
    )
    await check_probe(page, "A5b 同一張卡、cut 之前的 9.00 → 不跳", 9.00, 9.00)
    await check_probe(page, "A5c 同一張卡、cut 之後的 9.50 → 不跳", 9.50, 9.50)
    await check_probe(page, "A5d 卡間隙的 8.30 → 不跳（守門沒有變成整段亂跳）", 8.30, 8.30)

    # ══ 階段 B：卡 #5 起點與被刪的卡 #4 重疊 → 守衛必須護住 #5 的語音 ══
    V2SRT.write_text(
        _SRT_ORIG.replace(
            "00:00:08,550 --> 00:00:10,150", "00:00:07,500 --> 00:00:10,150"
        ),
        encoding="utf-8",
    )
    if not set_stage(offset=OFFSET_AB, cut_pad=PAD, cuts=[B_CUT_DISK], audio=None):
        C.check("B0 伺服器重啟（重疊卡）", False, "start_server 失敗", True)
        return C.summary()
    pageB, readyB = await open_pod(browser, ws, sessions)
    C.check("B0 卡 #5 頭與刪卡 #4 重疊的集開得起來", bool(readyB), bool(readyB), True)
    mB = await media_ready(pageB)
    C.check("B1 影片可 seek", bool(mB) and mB.get("seekable", 0) > 0, mB, "seekable>0")
    redsB = await red_keys(pageB)
    C.check("B2 cuts 換回紅卡 {4}（重疊不影響卡層換算）", redsB == ["4"], redsB, ["4"])

    ivB = backend_intervals()
    ok_ivB = (
        isinstance(ivB, list)
        and len(ivB) == 1
        and close(ivB[0][0] + OFFSET_AB, B_LO, 0.005)
        and close(ivB[0][1] + OFFSET_AB, B_HI, 0.005)
    )
    C.check(
        f"B3 後端區間 +1.5 = [{B_LO}, {B_HI}]",
        ok_ivB,
        ivB,
        [[B_LO - OFFSET_AB, B_HI - OFFSET_AB]],
    )

    await check_probe(pageB, f"B4a 沒有任何保留卡語音的 8.50 → 跳到 {B_HI}", 8.50, B_HI)
    await check_probe(
        pageB,
        "B4b 落在剪除區間內、但同時落在保留卡 #5 語音裡的 9.30 → 不跳"
        "（守衛護住保留卡；拿掉守衛就會跳到 9.75）",
        9.30,
        9.30,
    )

    # B5：把「後端會剪掉、預覽刻意不跳」這條已知落差釘成數字。兩側任一改了都會紅，
    # 逼人回頭重看，而不是讓落差靜靜擴大。
    card5_disp = [9.00, 11.65]
    ov = 0.0
    if ok_ivB:
        lo = max(ivB[0][0] + OFFSET_AB, card5_disp[0])
        hi = min(ivB[0][1] + OFFSET_AB, card5_disp[1])
        ov = round(max(0.0, hi - lo), 3)
    C.check(
        f"B5 已知落差（待裁決）：後端剪除區間侵入保留卡 #5 的語音 {B_OVERLAP}s，預覽刻意不跳過這段",
        close(ov, B_OVERLAP, 0.005),
        ov,
        B_OVERLAP,
    )
    extra["b5_backend_bites_into_kept_card_sec"] = ov

    V2SRT.write_text(_SRT_ORIG, encoding="utf-8")

    # ══ 階段 C（B3-2）：offset 0.123 ＋ 留著 sync_offset 但沒接音檔 ══
    if not set_stage(
        offset=C_OFFSET, cut_pad=0, cuts=None, audio={"sync_offset": C_SYNC}
    ):
        C.check("C0 伺服器重啟（軸位移階段）", False, "start_server 失敗", True)
        return C.summary()
    pageC, readyC = await open_pod(browser, ws, sessions)
    C.check("C0 帶 subtitle_offset_sec=0.123 的集開得起來", bool(readyC), bool(readyC), True)

    ep = await C.js(
        pageC,
        "(async () => { const r = await fetch('/api/episode', {cache:'no-store'});"
        " const j = await r.json();"
        " return {offset: j.subtitle_offset_sec, audio: j.audio}; })()",
    )
    aud = (ep or {}).get("audio") or {}
    ok_ep = (
        bool(ep)
        and close(ep.get("offset"), C_OFFSET, 1e-6)
        and close(aud.get("sync_offset"), C_SYNC, 1e-6)
        and not aud.get("path")
    )
    C.check(
        "C1 /api/episode 真的下放 audio.sync_offset=2.0 且沒有 path"
        "（守衛有被實際行使，不是空轉的測試）",
        ok_ep,
        ep,
        {"offset": C_OFFSET, "audio": {"sync_offset": C_SYNC, "path": None}},
    )

    txt = await card_time_text(pageC, C_IDX)
    lines = [x for x in (txt or "").split("\n")]
    ok_disp = len(lines) == 3 and lines[1] == C_DISP_S and lines[2] == C_DISP_E
    C.check(
        f"C2 卡 #{C_IDX} 卡面顯示 {C_DISP_S} / {C_DISP_E}"
        "（載入端守衛：沒守衛會減掉 sync_offset 變 0:05.47）",
        ok_disp,
        txt,
        f"#4 / {C_DISP_S} / {C_DISP_E}",
    )

    sel_btn = f'#cards-list .card[data-idx="{C_IDX}"] .card-time-edit-btn'
    clicked, top = await click_el(pageC, sel_btn)
    cls = top.get("cls") if isinstance(top, dict) else None
    cls_s = cls if isinstance(cls, str) else ""
    ok_btn = clicked and ("card-time-edit-btn" in cls_s)
    C.check("C3a ⏱ 鈕點得到，且該座標最上層就是它（沒被疊層吃掉）", ok_btn, top, "card-time-edit-btn")
    # 開工具列會自動起循環試聽（會一直播），暫停掉免得干擾後面讀值
    await C.js(pageC, "document.querySelector('#video').pause()")
    ins = await te_inputs(pageC, C_IDX)
    C.check(
        f"C3b ⏱ 工具列兩個輸入框也讀 {C_DISP_S} / {C_DISP_E}",
        ins == [C_DISP_S, C_DISP_E],
        ins,
        [C_DISP_S, C_DISP_E],
    )

    sel_minus = (
        f'#cards-list .card[data-idx="{C_IDX}"] .card-time-edit '
        '.te-btn[title^="起點 −0.1s"]'
    )
    ok_nudge_click, top2 = await click_el(pageC, sel_minus)
    await C.js(pageC, "document.querySelector('#video').pause()")
    ins2 = await te_inputs(pageC, C_IDX)
    C.check(
        f"C4 真點「起點 −0.1s」→ 輸入框變 {C_AFTER_NUDGE}（setCardTime 取整到 0.01s）",
        ok_nudge_click and ins2 == [C_AFTER_NUDGE, C_DISP_E],
        {"clicked": ok_nudge_click, "top": top2, "inputs": ins2},
        [C_AFTER_NUDGE, C_DISP_E],
    )

    ok_save = await do_save(pageC)
    C.check("C5 存檔完成（按鈕回報已儲存 + yaml mtime 前進 + 無錯誤 toast）", ok_save, ok_save, True)

    line = srt_card_line(C_IDX)
    extra["srt_card4_after_save"] = line
    C.check(
        f"C6 _v2.srt 卡 #{C_IDX} 寫成 {C_SRT_LINE}"
        "（存檔端守衛掉 → 09,247；取整退回 2 位小數 → 07,250）",
        line == C_SRT_LINE,
        line,
        C_SRT_LINE,
    )

    y = read_yaml()
    ok_rt = close(y.get("subtitle_offset_sec"), C_OFFSET, 1e-6) and close(
        ((y.get("audio") or {}).get("sync_offset")), C_SYNC, 1e-6
    )
    C.check(
        "C7 存檔沒有吃掉 subtitle_offset_sec / audio.sync_offset（round-trip 不掉值）",
        ok_rt,
        {"subtitle_offset_sec": y.get("subtitle_offset_sec"), "audio": y.get("audio")},
        {"subtitle_offset_sec": C_OFFSET, "audio": {"sync_offset": C_SYNC}},
    )

    if not start_server():
        C.check("C8 伺服器重啟（讀回）", False, "start_server 失敗", True)
        return C.summary()
    pageD, readyD = await open_pod(browser, ws, sessions)
    txt2 = await card_time_text(pageD, C_IDX)
    lines2 = [x for x in (txt2 or "").split("\n")]
    ok_back = (
        bool(readyD)
        and len(lines2) == 3
        and lines2[1] == C_AFTER_NUDGE
        and lines2[2] == C_DISP_E
    )
    C.check(
        f"C8 重載後卡 #{C_IDX} 讀回 {C_AFTER_NUDGE} / {C_DISP_E}（寫得進也讀得回，軸沒有漂）",
        ok_back,
        txt2,
        f"#4 / {C_AFTER_NUDGE} / {C_DISP_E}",
    )

    errs = []
    for p in (page, pageB, pageC, pageD):
        errs += [e for e in C.console_errors(p) if "favicon" not in str(e)]
    C.check("D 全程無 console 例外", not errs, errs[:3], [])

    bad = C.summary()
    OUT.write_text(
        json.dumps(
            {"results": C.results_as_dict(), "failed": bad, **extra},
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
        YAML.write_text(_YAML_ORIG, encoding="utf-8")
        V2SRT.write_text(_SRT_ORIG, encoding="utf-8")
        print("（已還原 episode.yaml 與 _v2.srt）", flush=True)
    sys.exit(1 if bad else 0)
