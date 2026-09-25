#!/usr/bin/env python3
"""D6 重構護欄：主編輯器「渲染指紋」走查。

為什麼要有這支：D6 要把 app.js 的渲染群（26 支 render*/apply*）整批搬到 render.js。
重構的驗收條件是**行為零變更**，而 diff 證明不了這件事（搬動本身就是整片 diff）。
所以改用指紋：對每個渲染容器抓正規化後的 outerHTML，動刀前錄 baseline、動刀後逐一
比對 —— 相同就是零行為變更，不同就指名是哪個容器、哪個狀態變了。

十二個狀態（每個狀態都先跑「非退化斷言」再抓指紋，避免對一個空 div 錄指紋還當成有覆蓋）：
  第一輪（單機集、無 speakers sidecar）
    S1  initial           t=0：字幕預覽空態、review 2 張、sus 1 張、兩條 ruler 都 hidden
    S2  caption-single    seek 到卡 3 顯示窗：單行字幕預覽（含 .caption-line 與內聯樣式）
    S3  time-toolbar      點卡 1 的 ⏱：.card-time-edit 進 #cards-list
    S4  review-seen-1     點卡 3「看過」：review 2→1、該卡掛 review-seen
    S5  review-empty      點卡 6「看過」：#review-toolbar 轉 hidden（空態分支）
    S6  sus-checked       勾卡 6 紅卡 checkbox：「已勾 1·約 N 秒」＋兩顆刪除鈕啟用
    S7  cap-size-dec      點字幕縮小：字級 −2、caption CSS 重算、未儲存徽章現身
    S11 capstyle-open     點「更多樣式」：renderCaptionStyleControls 把 9 個欄位填滿
    S12 trim-head-set     設頭 3.0s：renderTrimControls 的 head>0 分支（色帶／把手／提示）
  第二輪（雙機集＋speakers sidecar＋重疊卡，重起伺服器）
    S8  dual-initial      cam-ruler 與 speaker-ruler 都有段（含一段 .speaker-ruler-gap）
    S9  dual-overlap-cap  seek 到重疊窗：兩行字幕＋overlay 掛 multi-speaker
    S10 dual-cam-b        點卡 2 的 B 鈕：cam-ruler 重繪成 A/B 兩段以上

正規化只做三件事（規則集中在 Python 端的 norm()，JS 只回 raw outerHTML）：
  1. 摺疊標籤間空白 `>\\s+<` → `><`（index.html 的靜態縮排）
  2. 3 位以上小數統一 toFixed(2)（caption 樣式換算的浮點雜訊）
  3. 遮掉 #cards-list 的 data-last-render-ms  ※合成鍵 capStyleVals 不吃這三條，它是活值串（app.js:2592 寫入的渲染耗時，
     每次載入必然不同 —— 實測兩次載入 7.0 vs 6.9，是指紋唯一的真實雜訊源）

斷言紀律（README／MUTATIONS.md）：
  - 每個 ✓ 綁布林斷言；非退化斷言先過，指紋才有意義。
  - R0-STABILITY  同一狀態開兩次新頁 → 正規化指紋必須一致（指紋不穩，護欄就會假紅）。
                  同時回報「raw 是否也一致」，誠實標示正規化規則到底承不承重。
  - MUT-R1        改 reviewReasonLabel 的「半句結尾」→ 只有 #cards-list 指紋要變，其餘不變。
  - MUT-R2        同一個突變下，刻意過度正規化（剝掉所有文字節點與 title）的 loose 指紋
                  **不會變** → 反向證明「正規化吃掉真內容」會讓護欄瞎掉。
  - MUT-GREEN     還原後全部指紋回到 baseline。
  突變目標檔會在 app.js／render.js 之間自動尋址，所以動刀前後同一支走查都能跑。

前提：headless Chrome CDP（預設 :9522）已啟動；:8795 未被占用；沙盒集乾淨
（無 cameras、無 camB.mp4、無 speakers.json、srt 未被改過）。
跑法：
  動刀前錄 baseline： CDP_PORT=9522 /usr/bin/env python3 -u verify_render_fingerprint.py --record
  動刀後驗證：        CDP_PORT=9522 /usr/bin/env python3 -u verify_render_fingerprint.py
"""
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import cdp_common as C

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent.parent
STATIC = REPO_ROOT / "podcast_toolkit" / "web" / "static"
APP_JS = STATIC / "app.js"
RENDER_JS = STATIC / "render.js"  # D6 動刀後才存在；突變尋址會自動跳過不存在的檔

SANDBOX_ROOT = Path("/private/tmp/pt-timeline-baseline")
EP_DIR = SANDBOX_ROOT / "episode" / "20260601 時間軸基準集"
EP_YAML = EP_DIR / "episode.yaml"
SRT = EP_DIR / "03_成品" / "時間軸基準集_final_v2.srt"
SPEAKERS_JSON = EP_DIR / "03_成品" / "時間軸基準集_final_v2.speakers.json"
CAM_A_MP4 = EP_DIR / "01_母帶" / "時間軸基準集.mp4"
CAM_B_MP4 = EP_DIR / "01_母帶" / "camB.mp4"

CDP_PORT = int(os.environ.get("CDP_PORT", "9522"))
POD_PORT = 8795
POD = f"http://127.0.0.1:{POD_PORT}/?pthook=1"

BASELINE_JSON = HERE / "verify_render_fingerprint_baseline.json"
RESULT_JSON = HERE / "verify_render_fingerprint_result.json"
LOG_TXT = HERE / "verify_render_fingerprint_log.txt"
DIFF_DIR = HERE / "verify_render_fingerprint_diff"

RECORD = "--record" in sys.argv

# ── 沙盒 fixtures（跑完全部還原）────────────────────────────────────────
# 造一張待複查卡：卡 3 文字改成以「和」結尾（episode_io._flag_review 的半句尾字集）
SRT_REVIEW_OLD = "很多人以為錄音只要有支麥克風就好了"
SRT_REVIEW_NEW = "很多人以為錄音只要有支麥克風和"
# 造一張可疑空拍卡：卡 6 起點後移，前置 gap 由 0.9s 變 2.4s（big_gap_min_sec 預設 1.5）
SRT_GAP_OLD = "00:00:11,050 --> 00:00:13,050"
SRT_GAP_NEW = "00:00:12,550 --> 00:00:13,050"
# 第二輪造時間重疊：卡 4 起點提前到 6.00，與卡 3（4.25–6.65）重疊 0.65s
SRT_OVERLAP_OLD = "00:00:07,350 --> 00:00:08,250"
SRT_OVERLAP_NEW = "00:00:06,000 --> 00:00:08,250"
# 字幕預覽吃 episode.yaml 的 subtitle_offset_sec（本集 1.5s），
# 所以 seek 的影片時間 = SRT 時間 + 1.5（實測：卡 3 的 4.25–6.65 顯示於 5.75–8.15）。
SUB_OFFSET = 1.5
CARD3_SEEK = 6.5   # 卡 3（4.25–6.65）單獨顯示窗內
OVERLAP_SEEK = 7.8  # 卡 3 與改過的卡 4（6.00–8.25）顯示窗重疊區 7.50–8.15

YAML_CAMERAS = """cameras:
  a: 01_母帶/{name}.mp4
  b: 01_母帶/camB.mp4
"""
# 講者 sidecar：key 是 SRT 原本的 1-based 序號（srt_io.py:87）。
# 刻意漏掉第 5 張 → renderSpeakerRuler 的 .speaker-ruler-gap 分支才有真實態。
SPEAKERS_MAP = {
    "1": "a", "2": "a", "3": "b", "4": "a",
    "6": "b", "7": "b", "8": "a", "9": "a", "10": "b", "11": "b",
}
WANT_SPK_SEGS = 7  # a(1,2) b(3) a(4) gap(5) b(6,7) a(8,9) b(10,11)
WANT_SPK_GAPS = 1

# MUT-R1 目標：reviewReasonLabel 的標籤字串（同時進 flag.title 與 flag.innerHTML）
MUT_R1_OLD = 'half_sentence: "半句結尾"'
MUT_R1_NEW = 'half_sentence: "半句結尾ZZ"'

CONTAINERS = {
    "topbar": ".topbar-center",
    "cards": "#cards-list",
    "camRuler": "#cam-ruler",
    "spkRuler": "#speaker-ruler",
    "caption": "#caption-overlay",
    "sus": "#sus-toolbar",
    "review": "#review-toolbar",
    "capSize": "#caption-size",
    # D6 第三刀（renderTrimControls／renderCaptionStyleControls）的覆蓋：
    # 頭尾裁切的字樣／active 狀態在 .trim-controls，色帶與兩個把手是 .seek-wrap 的
    # 兄弟節點（各自吃 inline style），所以分成 5 個容器各自比。
    "trimCtl": ".trim-controls",
    "trimBandH": "#trim-band-head",
    "trimBandT": "#trim-band-tail",
    "trimHandH": "#trim-handle-head",
    "trimHandT": "#trim-handle-tail",
    "capStyle": "#cap-style-panel",
}

# 合成指紋：<input>／<select> 的 value／checked 是 IDL 屬性，設定它不會反映到
# outerHTML（只有 disabled 會）。renderCaptionStyleControls 寫的正是 value／checked，
# 光抓 #cap-style-panel 的 HTML 會漏掉它九成的輸出 —— 所以另外把面板欄位的活值
# 串成一個字串當第 14 個「容器」，一起進指紋。
SYNTH_KEYS = ["capStyleVals"]
FP_KEYS = list(CONTAINERS) + SYNTH_KEYS


# ── 伺服器 ───────────────────────────────────────────────────────────────
def port_busy(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
        return True
    except urllib.error.URLError:
        return False
    except Exception:
        return True


def start_server():
    log = open(SANDBOX_ROOT / "server-render-fingerprint.log", "ab")
    p = subprocess.Popen(
        [sys.executable, str(HERE / "serve_podcast.py")], stdout=log, stderr=log
    )
    for _ in range(120):
        if p.poll() is not None:
            raise RuntimeError("serve_podcast 啟動即退出，看 server-render-fingerprint.log")
        if port_busy(POD_PORT):
            time.sleep(0.6)
            return p
        time.sleep(0.25)
    raise RuntimeError("serve_podcast 30 秒內沒起來")


def stop_server(p):
    if p is None:
        return
    p.terminate()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(timeout=5)
    for _ in range(40):
        if not port_busy(POD_PORT):
            return
        time.sleep(0.25)


# ── 突變工具 ─────────────────────────────────────────────────────────────
def find_owner(needle: str) -> Path:
    """在 app.js／render.js 裡找出「恰有 1 處命中」的那一支。
    D6 動刀會把 reviewReasonLabel 搬去 render.js，尋址自動跟著走。"""
    hits = []
    for p in (APP_JS, RENDER_JS):
        if not p.exists():
            continue
        n = p.read_text(encoding="utf-8").count(needle)
        if n:
            hits.append((p, n))
    if len(hits) != 1 or hits[0][1] != 1:
        raise RuntimeError(f"突變尋址失敗：{needle!r} 命中 {hits}，預期恰好一檔一處")
    return hits[0][0]


def patch_file(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"預期 {path.name} 恰有 1 處命中 {old!r}，實際 {n} 處，拒絕突變")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def raw_eq(name, got, want):
    """突變／穩定性測試專用：印證據但不進 C.results 分母。"""
    ok = got == want
    print(f"    [{'PASS' if ok else 'FAIL'}] {name}  got={got!r} want={want!r}", flush=True)
    return ok


# ── 指紋正規化 ───────────────────────────────────────────────────────────
_WS_RE = re.compile(r">\s+<")
_FLOAT_RE = re.compile(r"-?\d+\.\d{3,}")
# renderCards 把自己的耗時寫進 dataset（app.js:2592），每次載入都不一樣。
# 這是唯一必須遮掉的雜訊源 —— 不遮的話 #cards-list 指紋每次都不同，護欄會恆紅。
_RENDER_MS_RE = re.compile(r'data-last-render-ms="[^"]*"')


def norm(html):
    if html is None:
        return None
    h = _WS_RE.sub("><", html).strip()
    h = _RENDER_MS_RE.sub('data-last-render-ms="<masked>"', h)
    return _FLOAT_RE.sub(lambda m: f"{float(m.group(0)):.2f}", h)


def fp(html):
    n = norm(html)
    if n is None:
        return None
    return {"sha": hashlib.sha256(n.encode("utf-8")).hexdigest()[:16], "len": len(n)}


def fps(html_map):
    return {k: fp(v) for k, v in html_map.items()}


# ── CDP helpers ──────────────────────────────────────────────────────────
def get_cdp_ws_url(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5) as r:
        return json.loads(r.read())["webSocketDebuggerUrl"]


async def wait_for(page, expr, timeout=25, interval=0.3):
    t = 0.0
    val = None
    while t < timeout:
        val = await C.js(page, expr)
        if val:
            return val
        await asyncio.sleep(interval)
        t += interval
    return val


async def open_pod(browser, ws, sessions):
    page = await C.open_page(browser, ws, sessions, POD, settle=2.0)
    cards = await wait_for(page, "document.querySelectorAll('#cards-list .card').length > 0")
    # caption 內聯樣式來自 computeCaptionScale()（依 .video-wrap 實際高度），
    # metadata 沒載完就抓到的是 scale=null 的另一種態 —— 先等載入再抓指紋。
    media = await wait_for(
        page,
        "(() => { const v = document.querySelector('#video');"
        " return !!(v && v.readyState >= 1 && v.seekable.length > 0); })()",
    )
    return page, bool(cards), bool(media)


async def close_page(page):
    if page is None:
        return
    try:
        await page.send("Page.close")
    except Exception:
        pass
    await asyncio.sleep(0.2)


async def click_selector(page, selector):
    """真滑鼠點擊 selector 中心；同時回報 elementFromPoint 的最上層是不是它自己
    （座標點擊會被 absolute 疊層吃掉，這一步是防「按了沒反應」靜默失敗）。"""
    sel = json.dumps(selector)
    info = await C.js(
        page,
        f"""(() => {{
          const el = document.querySelector({sel});
          if (!el) return {{ found: false }};
          el.scrollIntoView({{ block: 'center', inline: 'nearest' }});
          const r = el.getBoundingClientRect();
          const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
          const hit = document.elementFromPoint(cx, cy);
          return {{ found: true, cx, cy,
                    hit: !!(hit && hit.closest({sel})),
                    hitTag: hit ? hit.tagName : null,
                    hitCls: hit ? (hit.className || null) : null }};
        }})()""",
    )
    if not info or not info.get("found") or not info.get("hit"):
        return False, info
    await C.mouse_click(page, info["cx"], info["cy"])
    await asyncio.sleep(0.45)
    return True, info


async def seek(page, t):
    await C.js(page, f"(() => {{ document.querySelector('#video').currentTime = {t}; return 1; }})()")
    await asyncio.sleep(0.8)  # 等 timeupdate 驅動 renderCaption


# ── 頁面內量測 ───────────────────────────────────────────────────────────
JS_COLLECT = """(() => {
  const SELS = %s;
  // loose = 刻意過度正規化：clone 後把所有文字節點清空、所有 title 屬性拔掉。
  // 用來反向證明「正規化吃掉真內容」會讓護欄看不見真正的改動（MUT-R2）。
  const strip = (el) => {
    const c = el.cloneNode(true);
    const w = document.createTreeWalker(c, NodeFilter.SHOW_TEXT);
    const dead = [];
    while (w.nextNode()) dead.push(w.currentNode);
    for (const n of dead) n.textContent = '';
    for (const e of c.querySelectorAll('[title]')) e.removeAttribute('title');
    c.removeAttribute('title');
    return c.outerHTML;
  };
  const html = {}, loose = {};
  for (const k of Object.keys(SELS)) {
    const el = document.querySelector(SELS[k]);
    html[k] = el ? el.outerHTML : null;
    loose[k] = el ? strip(el) : null;
  }
  // 合成鍵（見 SYNTH_KEYS 註解）：面板欄位的活值，outerHTML 抓不到
  const capVals = [...document.querySelectorAll('#cap-style-panel input, #cap-style-panel select')]
    .map((e) => `${e.id}=${e.type === 'checkbox' ? (e.checked ? 1 : 0) : e.value}|${e.disabled ? 'd' : ''}`)
    .join(';');
  html['capStyleVals'] = capVals;
  loose['capStyleVals'] = capVals;
  const q = (s) => document.querySelector(s);
  const n = (s) => document.querySelectorAll(s).length;
  const ov = q('#caption-overlay');
  const capVal = q('#cap-size-val');
  const probe = {
    cards: n('#cards-list .card'),
    capLines: n('#caption-overlay .caption-line'),
    capMulti: !!(ov && ov.classList.contains('multi-speaker')),
    capFontSize: ov ? (ov.style.fontSize || '') : null,
    capText: ov ? ov.textContent : null,
    camHidden: !!(q('#cam-ruler') || {}).hidden,
    camSegs: n('#cam-ruler .cam-ruler-seg'),
    spkHidden: !!(q('#speaker-ruler') || {}).hidden,
    spkSegs: n('#speaker-ruler .speaker-ruler-seg'),
    spkGaps: n('#speaker-ruler .speaker-ruler-gap'),
    susHidden: !!(q('#sus-toolbar') || {}).classList.contains('hidden'),
    susCount: (q('#sus-count') || {}).textContent || null,
    susChecked: (q('#sus-checked-count') || {}).textContent || null,
    susDelDisabled: !!(q('#sus-delete-checked') || {}).disabled,
    reviewHidden: !!(q('#review-toolbar') || {}).classList.contains('hidden'),
    reviewCount: (q('#review-count') || {}).textContent || null,
    capSizeVal: capVal ? capVal.textContent : null,
    unsavedHidden: !!(q('#unsaved-badge') || {}).classList.contains('hidden'),
    saveDisabled: !!(q('#save-btn') || {}).disabled,
    teOpen: n('#cards-list .card-time-edit'),
    seenBtns: n('#cards-list .card-review-seen'),
    seenMarked: n('#cards-list .card-review-seen.is-seen'),
    camBCards: n('#cards-list .card.cam-b'),
    capStyleHidden: !!(q('#cap-style-panel') || {}).classList.contains('hidden'),
    capStyleGridHidden: !!(q('#cap-style-grid') || {}).classList.contains('hidden'),
    capFontName: (q('#cap-font-name') || {}).value ?? null,
    capPrimary: (q('#cap-primary-colour') || {}).value ?? null,
    capFieldsDisabled: n('#cap-style-panel input:disabled, #cap-style-panel select:disabled'),
    trimHeadVal: (q('#trim-head-val') || {}).textContent || null,
    trimTailVal: (q('#trim-tail-val') || {}).textContent || null,
    trimHeadActive: !!(q('#trim-head-btn') || {}).classList.contains('active'),
    trimBandHeadDisp: (q('#trim-band-head') || {}).style?.display ?? null,
    trimHandleHeadDisp: (q('#trim-handle-head') || {}).style?.display ?? null,
    trimHint: (q('#trim-hint') || {}).textContent || null,
  };
  return { html, loose, probe };
})()""" % json.dumps(CONTAINERS)


async def collect(page):
    out = await C.js(page, JS_COLLECT)
    if not out:
        raise RuntimeError("JS_COLLECT 回 None（頁面掛了？）")
    return out


def check_probe(state, pairs):
    """非退化斷言：每條都是 ok = 實得==期待 的布林比較。"""
    for label, got, want in pairs:
        C.check(f"{state} 非退化／{label}", got == want, got, want)


# ── 主流程 ───────────────────────────────────────────────────────────────
async def run_states_round1(browser, ws, sessions, snap):
    page, cards_ok, media_ok = await open_pod(browser, ws, sessions)
    C.check("R1 開場／卡片渲染", cards_ok, cards_ok, True)
    C.check("R1 開場／影片可 seek", media_ok, media_ok, True)

    # S1 initial（t=0：字幕預覽空態）
    await seek(page, 0.0)
    d = await collect(page)
    p = d["probe"]
    check_probe("S1", [
        ("卡數", p["cards"], 11),
        ("字幕預覽空態", p["capLines"], 0),
        ("caption 內聯字級有值", bool(p["capFontSize"]), True),
        ("review toolbar 顯示", p["reviewHidden"], False),
        ("review 張數", p["reviewCount"], "2"),
        ("sus toolbar 顯示", p["susHidden"], False),
        ("sus 張數", p["susCount"], "1"),
        ("sus 已勾 0", p["susChecked"], "已勾 0"),
        ("cam ruler 單機集隱藏", p["camHidden"], True),
        ("speaker ruler 無 sidecar 隱藏", p["spkHidden"], True),
        ("時間工具列未開", p["teOpen"], 0),
        ("看過鈕 2 顆", p["seenBtns"], 2),
        ("未儲存徽章隱藏", p["unsavedHidden"], True),
        # renderTrimControls 的 head=0/tail=0 分支：色帶與把手都藏著、提示是預設句
        ("裁切字樣 0.0s", (p["trimHeadVal"], p["trimTailVal"]), ("0.0s", "0.0s")),
        ("設頭鈕未 active", p["trimHeadActive"], False),
        ("頭色帶藏著", p["trimBandHeadDisp"], "none"),
        ("裁切提示是預設句", (p["trimHint"] or "").startswith("把播放游標"), True),
        # renderCaptionStyleControls 還沒被呼叫（面板未開）→ 欄位不該被動過
        ("字幕樣式面板未開", p["capStyleHidden"], True),
    ])
    snap["S1"] = d

    # S2 caption-single（seek 到卡 3 顯示窗 → 單行）
    await seek(page, CARD3_SEEK)
    d = await collect(page)
    p = d["probe"]
    check_probe("S2", [
        ("單行字幕", p["capLines"], 1),
        ("字幕文字命中卡 3", "麥克風和" in (p["capText"] or ""), True),
        ("未掛 multi-speaker", p["capMulti"], False),
    ])
    snap["S2"] = d

    # S3 time-toolbar（點卡 1 的 ⏱）
    ok, info = await click_selector(page, '#cards-list .card[data-idx="1"] .card-time-edit-btn')
    C.check("S3 點擊／⏱ 鈕最上層是它自己", ok, info, True)
    d = await collect(page)
    check_probe("S3", [("時間工具列已開", d["probe"]["teOpen"], 1)])
    snap["S3"] = d

    # S4 review-seen-1（點卡 3「看過」→ review 2→1）
    ok, info = await click_selector(page, '#cards-list .card[data-idx="3"] .card-review-seen')
    C.check("S4 點擊／卡 3 看過鈕", ok, info, True)
    d = await collect(page)
    p = d["probe"]
    check_probe("S4", [
        ("review 張數 2→1", p["reviewCount"], "1"),
        ("已標看過 1 張", p["seenMarked"], 1),
        ("review toolbar 仍顯示", p["reviewHidden"], False),
    ])
    snap["S4"] = d

    # S5 review-empty（點卡 6「看過」→ toolbar 轉 hidden）
    ok, info = await click_selector(page, '#cards-list .card[data-idx="6"] .card-review-seen')
    C.check("S5 點擊／卡 6 看過鈕", ok, info, True)
    d = await collect(page)
    p = d["probe"]
    check_probe("S5", [
        ("review toolbar 轉空態隱藏", p["reviewHidden"], True),
        ("已標看過 2 張", p["seenMarked"], 2),
        ("sus toolbar 不受影響", p["susHidden"], False),
    ])
    snap["S5"] = d

    # S6 sus-checked（勾卡 6 紅卡 checkbox）
    ok, info = await click_selector(page, '#cards-list .card[data-idx="6"] .card-sus-check')
    C.check("S6 點擊／卡 6 紅卡 checkbox", ok, info, True)
    d = await collect(page)
    p = d["probe"]
    check_probe("S6", [
        ("已勾 1 且帶秒數", (p["susChecked"] or "").startswith("已勾 1·約 "), True),
        ("刪除已勾鈕啟用", p["susDelDisabled"], False),
    ])
    snap["S6"] = d
    size_before = p["capSizeVal"]
    C.check("S6 非退化／字級是數字", (size_before or "").isdigit(), size_before, True)

    # S7 cap-size-dec（字級 −2）
    # 先展開「出片設定」：收合的 <details> 子元素量得到 rect 卻吃不到點擊
    # （Chrome 用 content-visibility:hidden），不展開就是靜默的「按了沒反應」。
    ok, info = await click_selector(page, ".framing-summary")
    C.check("S7 前置／展開出片設定面板", ok, info, True)
    opened = await C.js(page, "(() => !!document.querySelector('.framing-panel').open)()")
    C.check("S7 前置／面板已展開", opened, opened, True)
    ok, info = await click_selector(page, "#cap-size-dec")
    C.check("S7 點擊／字幕縮小鈕", ok, info, True)
    d = await collect(page)
    p = d["probe"]
    want_size = str(int(size_before) - 2) if (size_before or "").isdigit() else None
    check_probe("S7", [
        ("字級 −2", p["capSizeVal"], want_size),
        ("未儲存徽章現身", p["unsavedHidden"], False),
    ])
    snap["S7"] = d

    # S11 capstyle-open（點「更多樣式」→ renderCaptionStyleControls 把 9 個欄位填滿）
    ok, info = await click_selector(page, "#cap-style-btn")
    C.check("S11 點擊／更多樣式鈕", ok, info, True)
    d = await collect(page)
    p = d["probe"]
    check_probe("S11", [
        ("面板已開", p["capStyleHidden"], False),
        ("欄位格顯示（非 empty 態）", p["capStyleGridHidden"], False),
        ("欄位全部啟用", p["capFieldsDisabled"], 0),
        ("字體欄有值", bool(p["capFontName"]), True),
        ("文字色是 #rrggbb", bool(re.fullmatch(r"#[0-9a-f]{6}", p["capPrimary"] or "")), True),
    ])
    snap["S11"] = d

    # S12 trim-head-set（設頭 → renderTrimControls 的 head>0 分支：色帶／把手／保留提示）
    # 設頭讀的是 video.currentTime。S3 開過 ⏱ 工具列會啟動循環播放，不先暫停的話
    # 這 0.8 秒等待期間播放頭會自己往前跑（實測錄到 3.7s）→ 指紋每次都不一樣。
    await C.js(page, "(() => { const v = document.querySelector('#video'); v.pause(); return 1; })()")
    await seek(page, 3.0)
    now = await C.js(page, "(() => document.querySelector('#video').currentTime)()")
    C.check("S12 前置／播放頭停在 3.0 沒漂走", now == 3.0, now, 3.0)
    ok, info = await click_selector(page, "#trim-head-btn")
    C.check("S12 點擊／設頭鈕", ok, info, True)
    d = await collect(page)
    p = d["probe"]
    check_probe("S12", [
        ("裁切字樣 3.0s", p["trimHeadVal"], "3.0s"),
        ("設頭鈕轉 active", p["trimHeadActive"], True),
        ("頭色帶現身", p["trimBandHeadDisp"], "block"),
        ("頭把手現身", p["trimHandleHeadDisp"], "block"),
        ("提示改成保留秒數", (p["trimHint"] or "").startswith("保留 "), True),
    ])
    snap["S12"] = d

    await close_page(page)


async def run_states_round2(browser, ws, sessions, snap):
    page, cards_ok, media_ok = await open_pod(browser, ws, sessions)
    C.check("R2 開場／卡片渲染", cards_ok, cards_ok, True)
    C.check("R2 開場／影片可 seek", media_ok, media_ok, True)

    # S8 dual-initial（雙機 + speakers sidecar）
    await seek(page, 0.0)
    d = await collect(page)
    p = d["probe"]
    check_probe("S8", [
        ("卡數", p["cards"], 11),
        ("cam ruler 顯示", p["camHidden"], False),
        ("cam ruler 有段", p["camSegs"] >= 1, True),
        ("speaker ruler 顯示", p["spkHidden"], False),
        ("speaker 段數", p["spkSegs"], WANT_SPK_SEGS),
        ("speaker 缺漏灰段", p["spkGaps"], WANT_SPK_GAPS),
    ])
    snap["S8"] = d

    # S9 dual-overlap-caption（seek 到重疊窗 → 兩行 + multi-speaker）
    await seek(page, OVERLAP_SEEK)
    d = await collect(page)
    p = d["probe"]
    check_probe("S9", [
        ("兩行字幕", p["capLines"], 2),
        ("overlay 掛 multi-speaker", p["capMulti"], True),
    ])
    snap["S9"] = d

    # S10 dual-cam-b（點卡 2 的 B 鈕 → cam-ruler 重繪）
    ok, info = await click_selector(page, '#cards-list .card[data-idx="2"] .cam-b-btn')
    C.check("S10 點擊／卡 2 的 B 鈕", ok, info, True)
    d = await collect(page)
    p = d["probe"]
    check_probe("S10", [
        ("cam ruler 分成多段", p["camSegs"] >= 2, True),
        ("有卡染 cam-b", p["camBCards"] >= 1, True),
    ])
    snap["S10"] = d

    await close_page(page)


def compare_baseline(snap, baseline):
    """逐 (狀態, 容器) 比對指紋；不同就把兩邊完整 HTML 落檔供 diff。"""
    DIFF_DIR.mkdir(exist_ok=True)
    for state in sorted(snap):
        got = fps(snap[state]["html"])
        want = (baseline.get("states") or {}).get(state)
        if want is None:
            C.check(f"{state} 指紋／baseline 有這個狀態", False, None, "存在")
            continue
        for key in FP_KEYS:
            g, w = got.get(key), want.get(key)
            ok = g == w
            C.check(f"{state} 指紋／{key}", ok, g, w)
            if not ok:
                (DIFF_DIR / f"{state}-{key}-got.html").write_text(
                    norm(snap[state]["html"].get(key)) or "", encoding="utf-8"
                )
    for state in sorted((baseline.get("states") or {})):
        if state not in snap:
            C.check(f"{state} 指紋／本次有跑到這個狀態", False, None, "有跑")


async def main():
    print(f"=== D6 渲染指紋走查（{'錄 baseline' if RECORD else '驗證'}）===", flush=True)

    # 前提檢查
    if port_busy(POD_PORT):
        print(f"!! :{POD_PORT} 已被占用，先停掉再跑", flush=True)
        return 1
    base_yaml = EP_YAML.read_text(encoding="utf-8")
    base_srt = SRT.read_text(encoding="utf-8")
    if "cameras:" in base_yaml:
        print("!! episode.yaml 已有 cameras，沙盒不乾淨", flush=True)
        return 1
    if SRT_REVIEW_NEW in base_srt or SRT_GAP_NEW in base_srt or SRT_OVERLAP_NEW in base_srt:
        print("!! srt 殘留上次走查的改動，先還原", flush=True)
        return 1
    if CAM_B_MP4.exists() or SPEAKERS_JSON.exists():
        print("!! camB.mp4／speakers.json 殘留，先清掉", flush=True)
        return 1
    if not RECORD and not BASELINE_JSON.exists():
        print(f"!! 找不到 baseline {BASELINE_JSON.name}，先用 --record 在動刀前錄一份", flush=True)
        return 1

    src_snapshot = {p: p.read_text(encoding="utf-8") for p in (APP_JS, RENDER_JS) if p.exists()}
    snap = {}
    server = None
    ws = None
    browser = sessions = None
    try:
        # 第一輪 fixtures：待複查卡 + 可疑空拍卡
        t = base_srt.replace(SRT_REVIEW_OLD, SRT_REVIEW_NEW).replace(SRT_GAP_OLD, SRT_GAP_NEW)
        SRT.write_text(t, encoding="utf-8")

        server = start_server()
        browser, sessions, ws = await C.connect(get_cdp_ws_url(CDP_PORT))

        print("\n-- 第一輪：單機集（S1–S7）--", flush=True)
        await run_states_round1(browser, ws, sessions, snap)

        # 穩定性／突變（只在驗證模式跑；錄 baseline 時不動原始碼）
        if not RECORD:
            print("\n-- R0-STABILITY：同一狀態開兩次新頁 --", flush=True)
            page, _, _ = await open_pod(browser, ws, sessions)
            await seek(page, 0.0)
            a = await collect(page)
            await close_page(page)
            page, _, _ = await open_pod(browser, ws, sessions)
            await seek(page, 0.0)
            b = await collect(page)
            await close_page(page)
            norm_same = fps(a["html"]) == fps(b["html"])
            raw_diff = sorted(k for k in FP_KEYS if a["html"][k] != b["html"][k])
            C.check("R0-STABILITY 正規化指紋跨兩次載入一致", norm_same, norm_same, True)
            # raw 相不相同取決於當次的渲染耗時碰巧撞不撞號（實測兩次載入曾是 7.0 vs 6.9，
            # 也曾完全相同），所以它不當斷言、只如實印出來。
            print(f"    [資訊] raw outerHTML 有差異的容器：{raw_diff or '無（本次碰巧撞號）'}", flush=True)

            # MASK-R3：用確定性的方式證明「遮掉 data-last-render-ms」這條規則是承重的，
            # 不靠上面那個看運氣的 raw 比較。把同一份 HTML 的耗時值換掉：raw 必須不同、
            # 正規化後必須相同 —— 否則護欄會因為一個純計時數字而恆紅。
            real = a["html"]["cards"]
            faked = _RENDER_MS_RE.sub('data-last-render-ms="999.9"', real)
            C.check("MASK-R3 渲染耗時是真雜訊（換個值 raw 就不同）", faked != real,
                    faked != real, True)
            C.check("MASK-R3 遮罩規則把這個雜訊吃掉（正規化後相同）",
                    fp(faked) == fp(real), fp(faked) == fp(real), True)

            print("\n-- MUT-R1／R2：改 reviewReasonLabel 的標籤字串 --", flush=True)
            owner = find_owner(MUT_R1_OLD)
            print(f"    突變目標：{owner.name}", flush=True)
            patch_file(owner, MUT_R1_OLD, MUT_R1_NEW)
            try:
                page, _, _ = await open_pod(browser, ws, sessions)
                await seek(page, 0.0)
                m = await collect(page)
                await close_page(page)
                base_fp = fps(snap["S1"]["html"])
                mut_fp = fps(m["html"])
                changed = {k for k in FP_KEYS if base_fp[k] != mut_fp[k]}
                ok1 = raw_eq("MUT-R1 只有 #cards-list 指紋改變", sorted(changed), ["cards"])
                base_loose = fps(snap["S1"]["loose"])
                mut_loose = fps(m["loose"])
                loose_changed = {k for k in FP_KEYS if base_loose[k] != mut_loose[k]}
                ok2 = raw_eq("MUT-R2 過度正規化的 loose 指紋看不見這個改動",
                             sorted(loose_changed), [])
                C.check("MUT-R1 指紋抓到渲染輸出改變且只動到對應容器", ok1, sorted(changed), ["cards"])
                C.check("MUT-R2 反向確認：正規化吃掉真內容就會瞎掉", ok2, sorted(loose_changed), [])
            finally:
                owner.write_text(src_snapshot[owner], encoding="utf-8")

            print("\n-- MUT-GREEN：還原後 S1 指紋回到走查本身的量測 --", flush=True)
            page, _, _ = await open_pod(browser, ws, sessions)
            await seek(page, 0.0)
            g = await collect(page)
            await close_page(page)
            C.check("MUT-GREEN 還原後 S1 指紋與突變前相同",
                    fps(g["html"]) == fps(snap["S1"]["html"]),
                    fps(g["html"]) == fps(snap["S1"]["html"]), True)

        # 第二輪 fixtures：雙機 + speakers sidecar + 重疊卡（需重起伺服器讀新 cfg）
        print("\n-- 第二輪：雙機集 + speakers sidecar（S8–S10）--", flush=True)
        stop_server(server)
        server = None
        shutil.copyfile(CAM_A_MP4, CAM_B_MP4)
        EP_YAML.write_text(base_yaml + YAML_CAMERAS, encoding="utf-8")
        SPEAKERS_JSON.write_text(
            json.dumps(SPEAKERS_MAP, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        SRT.write_text(
            SRT.read_text(encoding="utf-8").replace(SRT_OVERLAP_OLD, SRT_OVERLAP_NEW),
            encoding="utf-8",
        )
        server = start_server()
        await run_states_round2(browser, ws, sessions, snap)

        # 比對／落檔
        if RECORD:
            BASELINE_JSON.write_text(
                json.dumps(
                    {
                        "note": "D6 動刀前錄的渲染指紋 baseline；只存 sha256[:16] 與長度",
                        "containers": {**CONTAINERS, "capStyleVals": "(合成：面板欄位活值)"},
                        "states": {s: fps(snap[s]["html"]) for s in sorted(snap)},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            print(f"\n已寫入 baseline：{BASELINE_JSON.name}（{len(snap)} 個狀態）", flush=True)
        else:
            print("\n-- 指紋比對 baseline --", flush=True)
            compare_baseline(snap, json.loads(BASELINE_JSON.read_text(encoding="utf-8")))

    finally:
        stop_server(server)
        EP_YAML.write_text(base_yaml, encoding="utf-8")
        SRT.write_text(base_srt, encoding="utf-8")
        for p in (CAM_B_MP4, SPEAKERS_JSON):
            if p.exists():
                p.unlink()
        for p, text in src_snapshot.items():
            if p.read_text(encoding="utf-8") != text:
                p.write_text(text, encoding="utf-8")
                print(f"[還原] {p.name} 被突變留下的改動已復原", flush=True)
        print("[還原] episode.yaml／srt／camB.mp4／speakers.json 全部復原", flush=True)
        if ws is not None:
            await ws.close()

    bad = C.summary()
    RESULT_JSON.write_text(
        json.dumps(
            {"mode": "record" if RECORD else "verify", "results": C.results_as_dict()},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
