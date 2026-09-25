#!/usr/bin/env python3
"""F 梯驗收走查：主編輯器三顆小缺陷（F1 快捷鍵併軌／F2 鏡頭鈕 tooltip／F3 操作欄固定寬）。

驗的三條鏈：
  F1  static/shortcuts.js 的 SHORTCUTS 單一資料表
      → renderShortcutsModal() 寫進 #shortcuts-modal 的 <dl>（modal 總覽）
      → timeToolbarHintHtml() 寫進 .te-hints（⏱ 時間工具列提示列）
      併軌前這兩處各抄一份且已漂移（[ ] 打點、P 循環只進提示列）。
  F2  camBtnTitle(which, eff, mapped) → cam-a-btn / cam-b-btn 的 title
      舊碼判準是「mapping 存不存在」而非它的值，於是 explicit 標 b 的卡上
      A 鈕寫成「目前鏡頭（已 explicit 標記）」——兩句都錯。四態各驗一次。
  F3  .card / .card.card-has-cam 末欄改吃 --card-actions-w 固定寬
      舊碼末欄是 auto，而每張 .card 是獨立 grid container，寬度由「這張卡有哪幾顆鈕」
      決定（28／46／56px 三種），文字欄右緣跟著參差，點一下「看過」整張卡的字還會右彈。

斷言紀律（README／MUTATIONS.md）：
  - 每個 ✓ 綁布林斷言 ok=實得==期待；只印值不斷言的行不算測過。
  - 可點元素一律驗 elementFromPoint 最上層真的是它（座標點擊會被疊層吃掉）。
  - 開頭先驗版本指紋（原始碼＋DOM），避免測到未同步的舊碼。
  - 四項逐一突變（不是只驗全開 vs 全關）：
      MUT-F1  把資料表的一條 hint/desc 改字 → 提示列與 modal 兩個消費端都要跟著變
              （證明兩處真的都從同一份表渲染，不是只有一邊接線）
      MUT-F2  把 camBtnTitle 的兩個呼叫端還原成舊三元 → 四態驗證變紅
      MUT-F3A 把 .card 的 --card-actions-w 換回 auto → 單機集右緣參差回到 18px
      MUT-F3B 把 .card.card-has-cam 末欄換回 auto → 雙機集右緣參差回來
              （這條規則會整條蓋掉 .card 的 grid-template-columns，是獨立的一半）

F2 需要 cam B，而 Episode 的 cfg 只在建構時讀一次，所以本檔自己起／停
serve_podcast.py（:8795）：第一輪單機集（驗 F1／F3 單機），patch episode.yaml 加
cameras.b 後重起第二輪（驗 F2／F3 雙機）。待複查卡靠改 srt 造（_flag_review 看
「文字長度 ≥4 且結尾落在半句尾字集」），跑完 yaml／srt／camB.mp4 全部還原。

前提：headless Chrome CDP（預設 :9522）已啟動；:8795 未被占用。
跑法：CDP_PORT=9522 /usr/bin/env python3 -u verify_editor_small_defects.py
"""
import asyncio
import json
import os
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
APP_CSS = STATIC / "app.css"
INDEX_HTML = STATIC / "index.html"
SHORTCUTS_JS = STATIC / "shortcuts.js"

SANDBOX_ROOT = Path("/private/tmp/pt-timeline-baseline")
EP_DIR = SANDBOX_ROOT / "episode" / "20260601 時間軸基準集"
EP_YAML = EP_DIR / "episode.yaml"
SRT = EP_DIR / "03_成品" / "時間軸基準集_final_v2.srt"
CAM_A_MP4 = EP_DIR / "01_母帶" / "時間軸基準集.mp4"
CAM_B_MP4 = EP_DIR / "01_母帶" / "camB.mp4"

CDP_PORT = int(os.environ.get("CDP_PORT", "9522"))
POD_PORT = 8795
POD = f"http://127.0.0.1:{POD_PORT}/?pthook=1"

RESULT_JSON = HERE / "verify_editor_small_defects_result.json"
LOG_TXT = HERE / "verify_editor_small_defects_log.txt"

# F1-b 的凍結字面字串：併軌前 app.js 裡那行手抄的提示列 HTML（git diff 取得）。
# 併軌是重構，行為不該變 —— 資料表推出來的 HTML 必須與這串逐字相同。
FROZEN_HINT = (
    "<kbd>←→</kbd> 起點｜<kbd>⌥←→</kbd> 訖點｜<kbd>Shift</kbd> ×5｜<kbd>⌘</kbd> ×10｜"
    "<kbd>[</kbd> <kbd>]</kbd> 設為播放位置｜<kbd>P</kbd> 循環"
)
WANT_SHORTCUT_ROWS = 17  # 資料表條數（計畫檔 F1-a）

# 造待複查卡：把第 3 張卡的文字改成以「和」結尾（resegment._HALF_SENTENCE_TAIL）
SRT_OLD_LINE = "很多人以為錄音只要有支麥克風就好了"
SRT_NEW_LINE = "很多人以為錄音只要有支麥克風和"

YAML_CAMERAS = """cameras:
  a: 01_母帶/{name}.mp4
  b: 01_母帶/camB.mp4
"""

# F2 四態的期待字串（camBtnTitle 產生）
T_A_EXPLICIT = "鏡頭 A：目前鏡頭（已 explicit 標記）"
T_A_INHERIT = "鏡頭 A：目前鏡頭（沿用前一張）"
T_A_SWITCH = "鏡頭 A：切到 A 鏡頭"
T_B_EXPLICIT = "鏡頭 B：目前鏡頭（已 explicit 標記）"
T_B_INHERIT = "鏡頭 B：目前鏡頭（沿用前一張）"
T_B_SWITCH = "鏡頭 B：切到 B 鏡頭"

# MUT-F2 的還原目標（併軌前的舊三元，git diff 原文）
MUT_F2_A_NEW = '      aBtn.title = camBtnTitle("a", eff, state.camerasMapping.get(key));'
MUT_F2_A_OLD = """      aBtn.title = state.camerasMapping.get(key)
        ? "鏡頭 A：目前鏡頭（已 explicit 標記）"
        : "鏡頭 A：目前鏡頭（沿用前一張）";"""
MUT_F2_B_NEW = '      bBtn.title = camBtnTitle("b", eff, state.camerasMapping.get(key));'
MUT_F2_B_OLD = """      bBtn.title = state.camerasMapping.get(key)
        ? "鏡頭 B：切到 B 鏡頭（已 explicit 標記）"
        : "鏡頭 B：切到 B 鏡頭";"""


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
    log = open(SANDBOX_ROOT / "server-editor-small-defects.log", "ab")
    p = subprocess.Popen(
        [sys.executable, str(HERE / "serve_podcast.py")], stdout=log, stderr=log
    )
    for _ in range(120):
        if p.poll() is not None:
            raise RuntimeError("serve_podcast 啟動即退出，看 server-editor-small-defects.log")
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


def patch_file(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"預期 {path.name} 恰有 1 處命中 {old!r}，實際 {n} 處，拒絕突變")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def raw_eq(name, got, want):
    """突變測試專用：不進 C.results 分母，只印證據。"""
    ok = got == want
    print(f"    [{'PASS' if ok else 'FAIL'}] {name}  got={got!r} want={want!r}", flush=True)
    return ok, got


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
    ready = await wait_for(
        page, "document.querySelectorAll('#cards-list .card').length > 0"
    )
    return page, ready


async def close_page(page):
    if page is None:
        return
    try:
        await page.send("Page.close")
    except Exception:
        pass
    await asyncio.sleep(0.2)


async def click_selector(page, selector):
    """真滑鼠點擊 selector 中心，並回報 (點到了沒, 最上層元素資訊)。
    座標點擊跟真實使用者一樣會被疊層吃掉，所以量座標的同時把
    elementFromPoint 的結果一起帶回來（hit=最上層是否就是它自己）。"""
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
          return {{ found: true, cx, cy, w: r.width, h: r.height,
                    hit: !!(hit && hit.closest({sel})),
                    hitTag: hit ? hit.tagName : null,
                    hitCls: hit ? (hit.className || null) : null }};
        }})()""",
    )
    if not info or not info.get("found"):
        return False, info
    await C.mouse_click(page, info["cx"], info["cy"])
    await asyncio.sleep(0.45)
    return True, info


async def press_key(page, key, text=None):
    base = {"key": key}
    if text is not None:
        base["text"] = text
    await page.send("Input.dispatchKeyEvent", dict(base, type="keyDown"))
    await page.send("Input.dispatchKeyEvent", dict(base, type="keyUp"))
    await asyncio.sleep(0.4)


# ── 頁面內量測 ───────────────────────────────────────────────────────────
JS_MODAL = """(() => {
  const dlg = document.querySelector('#shortcuts-modal');
  const dl = dlg && dlg.querySelector('.shortcuts-list');
  if (!dl) return { err: 'no-dl' };
  const dts = [...dl.querySelectorAll('dt')].map(d => d.textContent);
  const dds = [...dl.querySelectorAll('dd')].map(d => d.textContent);
  return {
    open: !!dlg.open,
    dt: dts.length,
    dd: dds.length,
    bracket: dts.some(t => t.includes('[')) && dts.some(t => t.includes(']')),
    ploop: dds.some(t => t.includes('切換循環試聽')),
    errRows: dl.querySelectorAll('.shortcuts-error').length,
    errText: (dl.querySelector('.shortcuts-error') || {}).textContent || null,
    html: dl.innerHTML,
  };
})()"""

JS_HINTS = """(() => {
  const h = document.querySelector('#cards-list .card-time-edit .te-hints');
  if (!h) return { found: false };
  return {
    found: true,
    html: h.innerHTML,
    errRows: h.querySelectorAll('.te-hint-error').length,
  };
})()"""

JS_CARD_GEOM = """(() => {
  const cards = [...document.querySelectorAll('#cards-list .card')];
  const rows = cards.map((c, i) => {
    const txt = c.querySelector('.card-text');
    const act = c.querySelector('.card-actions');
    const seen = c.querySelector('.card-review-seen');
    return {
      i,
      idx: c.dataset.idx,
      review: !!seen,
      seenLabel: seen ? seen.textContent.trim() : null,
      isSeen: seen ? seen.classList.contains('is-seen') : null,
      hasCam: c.classList.contains('card-has-cam'),
      txtR: txt ? Math.round(txt.getBoundingClientRect().right * 100) / 100 : null,
      actW: act ? Math.round(act.getBoundingClientRect().width * 100) / 100 : null,
    };
  });
  const uniq = (a) => [...new Set(a)].sort((x, y) => x - y);
  return {
    n: rows.length,
    rows,
    txtRs: uniq(rows.map(r => r.txtR)),
    actWs: uniq(rows.map(r => r.actW)),
    nReview: rows.filter(r => r.review).length,
    nPlain: rows.filter(r => !r.review).length,
  };
})()"""

JS_CAM = """(() => {
  const cards = [...document.querySelectorAll('#cards-list .card')].slice(0, 4);
  return cards.map((c, i) => {
    const a = c.querySelector('.cam-a-btn');
    const b = c.querySelector('.cam-b-btn');
    return {
      i,
      idx: c.dataset.idx,
      aTitle: a ? a.title : null,
      bTitle: b ? b.title : null,
      aActive: a ? a.classList.contains('active') : null,
      bActive: b ? b.classList.contains('active') : null,
    };
  });
})()"""


def geom_spread(g):
    """右緣參差幅度（px）：最大 txtR − 最小 txtR。"""
    if not g or not g.get("txtRs"):
        return None
    return round(max(g["txtRs"]) - min(g["txtRs"]), 2)


async def open_modal_via_button(page):
    ok, top = await click_selector(page, "#shortcuts-btn")
    info = await C.js(page, JS_MODAL)
    return ok, top, info


async def open_time_toolbar(page, nth=1):
    idx = await C.js(
        page,
        "(() => { const c = document.querySelectorAll('#cards-list .card');"
        f" return c[{nth - 1}] ? c[{nth - 1}].dataset.idx : null; }})()",
    )
    sel = f'#cards-list .card[data-idx="{idx}"] .card-time-edit-btn'
    ok, top = await click_selector(page, sel)
    info = await C.js(page, JS_HINTS)
    return ok, top, info


async def click_cam(page, nth, which):
    """點第 nth 張卡（1-based）的 A 或 B 膠囊；卡片一律用 data-idx 定位。"""
    idx = await C.js(
        page,
        "(() => { const c = document.querySelectorAll('#cards-list .card');"
        f" return c[{nth - 1}] ? c[{nth - 1}].dataset.idx : null; }})()",
    )
    return await click_selector(page, f'#cards-list .card[data-idx="{idx}"] .cam-{which}-btn')


async def main():
    if port_busy(POD_PORT):
        print(f"[ABORT] :{POD_PORT} 已被占用，本檔要自己管理 serve_podcast，請先停掉", flush=True)
        return 2

    base_yaml = EP_YAML.read_text(encoding="utf-8")
    if "cameras" in base_yaml:
        print("[ABORT] 沙盒 episode.yaml 已含 cameras，先還原再跑", flush=True)
        return 2
    base_srt = SRT.read_text(encoding="utf-8")
    if SRT_NEW_LINE in base_srt:
        print("[ABORT] 沙盒 srt 已含走查的待複查卡字串，先還原再跑", flush=True)
        return 2
    if CAM_B_MP4.exists():
        print("[ABORT] 沙盒已有 camB.mp4 殘留，先清掉再跑", flush=True)
        return 2
    src_snapshot = {
        p: p.read_text(encoding="utf-8") for p in (APP_JS, APP_CSS, SHORTCUTS_JS)
    }

    log_lines = []

    def logp(s=""):
        print(s, flush=True)
        log_lines.append(str(s))

    ws_url = get_cdp_ws_url(CDP_PORT)
    browser, sessions, ws = await C.connect(ws_url)
    server = None
    try:
        logp("=" * 70)
        logp(f"F 梯小缺陷走查（F1 快捷鍵併軌／F2 鏡頭鈕 tooltip／F3 操作欄固定寬）")
        logp(f"URL={POD}  CDP={CDP_PORT}")
        logp("=" * 70)

        # 造待複查卡（第 3 張）
        patch_file(SRT, SRT_OLD_LINE, SRT_NEW_LINE)

        # ══════════ 第一輪：單機集（F1／F3 單機） ══════════
        server = start_server()
        page, ready = await open_pod(browser, ws, sessions)

        logp("\n--- V0 版本指紋（避免測到未同步的舊碼）---")
        C.check("V0.0 編輯器載入且卡片列表有卡", bool(ready), ready, True)
        src_fp = {
            "shortcutsTable": "export const SHORTCUTS = [" in SHORTCUTS_JS.read_text(encoding="utf-8"),
            "appImports": 'from "./shortcuts.js"' in APP_JS.read_text(encoding="utf-8"),
            "camBtnTitle": "function camBtnTitle(which, eff, mapped)" in APP_JS.read_text(encoding="utf-8"),
            "dlEmptyInHtml": '<dl class="shortcuts-list"></dl>' in INDEX_HTML.read_text(encoding="utf-8"),
            "actionsVar": "--card-actions-w: 58px;" in APP_CSS.read_text(encoding="utf-8"),
            "hasCamVar": "1fr auto var(--card-actions-w)" in APP_CSS.read_text(encoding="utf-8"),
        }
        want_fp = {k: True for k in src_fp}
        C.check("V0.1 原始碼指紋：三顆缺陷的修法都在服務中的原始碼樹裡", src_fp == want_fp, src_fp, want_fp)
        dom_fp = await C.js(
            page,
            "(() => { const dl = document.querySelector('#shortcuts-modal .shortcuts-list');"
            " return { dlExists: !!dl, dlEmptyBeforeOpen: dl ? dl.children.length === 0 : null,"
            " modalOpen: !!document.querySelector('#shortcuts-modal').open }; })()",
        )
        C.check(
            "V0.2 DOM 指紋：<dl> 存在且開啟前是空的（清單由 openShortcuts() 每次渲染）",
            dom_fp == {"dlExists": True, "dlEmptyBeforeOpen": True, "modalOpen": False},
            dom_fp, {"dlExists": True, "dlEmptyBeforeOpen": True, "modalOpen": False},
        )

        logp("\n--- F1 快捷鍵清單併軌 ---")
        ok_btn, top_btn, m1 = await open_modal_via_button(page)
        C.check("F1-a.0 ? 鈕點得到（elementFromPoint 最上層是它自己）",
                bool(ok_btn and top_btn.get("hit")), {"clicked": ok_btn, "top": top_btn}, True)
        got_a = {"open": m1.get("open"), "dt": m1.get("dt"), "dd": m1.get("dd"),
                 "bracket": m1.get("bracket"), "ploop": m1.get("ploop"),
                 "errRows": m1.get("errRows")}
        want_a = {"open": True, "dt": WANT_SHORTCUT_ROWS, "dd": WANT_SHORTCUT_ROWS,
                  "bracket": True, "ploop": True, "errRows": 0}
        C.check(f"F1-a modal 渲染出 {WANT_SHORTCUT_ROWS} 組 dt/dd，且含 [ ] 與 P 循環（併軌前 modal 缺這兩條）",
                got_a == want_a, got_a, want_a)
        html_btn = m1.get("html")

        # F1-d：? 鍵開的內容要與 ? 鈕開的一致（同一個渲染函式）
        await click_selector(page, "#shortcuts-close")
        closed = await C.js(page, "!!document.querySelector('#shortcuts-modal').open")
        await press_key(page, "?", "?")
        m2 = await C.js(page, JS_MODAL)
        got_d = {"closedInBetween": closed, "openByKey": m2.get("open"),
                 "sameHtml": m2.get("html") == html_btn, "dt": m2.get("dt")}
        want_d = {"closedInBetween": False, "openByKey": True, "sameHtml": True,
                  "dt": WANT_SHORTCUT_ROWS}
        C.check("F1-d ? 鈕與 ? 鍵開出同一份內容（逐字相同）", got_d == want_d, got_d, want_d)
        await click_selector(page, "#shortcuts-close")

        ok_te, top_te, h1 = await open_time_toolbar(page, nth=1)
        got_b = {"teBtnClickable": bool(ok_te and top_te.get("hit")),
                 "found": h1.get("found"), "html": h1.get("html"), "errRows": h1.get("errRows")}
        want_b = {"teBtnClickable": True, "found": True, "html": FROZEN_HINT, "errRows": 0}
        C.check("F1-b 提示列 HTML 與併軌前的字面字串逐字相同（重構無行為變更）",
                got_b == want_b, got_b, want_b)
        await press_key(page, "Escape")

        errs1 = C.console_errors(page)
        C.check("F1/F3 單機輪：無未捕捉的 console 例外", errs1 == [], errs1, [])

        logp("\n--- F3 卡片操作欄固定寬（單機集）---")
        g1 = await C.js(page, JS_CARD_GEOM)
        fixture_ok = {"nReview": g1.get("nReview") >= 1, "nPlain": g1.get("nPlain") >= 1,
                      "hasCam": any(r["hasCam"] for r in g1.get("rows", []))}
        C.check("F3-fixture 這一輪確實同時有待複查卡與一般卡，且是單機集（無 cam 欄）",
                fixture_ok == {"nReview": True, "nPlain": True, "hasCam": False},
                fixture_ok, {"nReview": True, "nPlain": True, "hasCam": False})
        got_f3 = {"txtRs": g1.get("txtRs"), "actWs": g1.get("actWs"), "spread": geom_spread(g1)}
        want_f3 = {"txtRs": g1.get("txtRs")[:1], "actWs": [58.0], "spread": 0}
        C.check("F3-a 單機集：全卡 .card-text 右緣同一個 x（參差 0px）、操作欄恆 58px",
                len(g1.get("txtRs", [])) == 1 and g1.get("actWs") == [58.0] and geom_spread(g1) == 0,
                got_f3, want_f3)
        base_txt_r = g1["txtRs"][0]

        # 「看過」兩態都不能改變版面（舊碼點一下字就右彈 10px）
        review_idx = next(r["idx"] for r in g1["rows"] if r["review"])
        review_sel = f'#cards-list .card[data-idx="{review_idx}"]'
        seen_sel = f"{review_sel} .card-review-seen"
        ok_seen, top_seen = await click_selector(page, seen_sel)
        g2 = await C.js(page, JS_CARD_GEOM)
        got_seen = {"clickable": bool(ok_seen and top_seen.get("hit")),
                    "isSeen": next(r["isSeen"] for r in g2["rows"] if r["review"]),
                    "label": next(r["seenLabel"] for r in g2["rows"] if r["review"]),
                    "txtRs": g2.get("txtRs")}
        want_seen = {"clickable": True, "isSeen": True, "label": "已看過", "txtRs": [base_txt_r]}
        C.check("F3-a2 點「看過」→「已看過」後右緣不動（舊碼整張卡的字右彈 10px）",
                got_seen == want_seen, got_seen, want_seen)

        # F3-b：三顆操作鈕在固定寬欄裡都還點得到（沒被裁掉、沒被疊層吃掉）
        btn_tops = {}
        for name, cls in (("del", "card-del"), ("timeEdit", "card-time-edit-btn"),
                          ("seen", "card-review-seen")):
            sel = f"{review_sel} .{cls}"
            inside = await C.js(
                page,
                f"""(() => {{
                  const el = document.querySelector({json.dumps(sel)});
                  if (!el) return {{ found: false }};
                  el.scrollIntoView({{ block: 'center', inline: 'nearest' }});
                  const a = el.closest('.card-actions');
                  const r = el.getBoundingClientRect(), ar = a.getBoundingClientRect();
                  const hit = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
                  return {{ clipped: el.scrollWidth > el.clientWidth + 1,
                            overflowRight: Math.round((r.right - ar.right) * 100) / 100 > 0.5,
                            topIsSelf: !!(hit && hit.closest({json.dumps(sel)})),
                            hitTag: hit ? hit.tagName : null,
                            hitCls: hit ? (hit.className || null) : null }};
                }})()""",
            )
            btn_tops[name] = {k: v for k, v in (inside or {}).items()
                              if k in ("clipped", "overflowRight", "topIsSelf")}
            if not btn_tops[name].get("topIsSelf"):
                logp(f"    （F3-b 診斷）{name} 最上層是 {(inside or {}).get('hitTag')}"
                     f" / {(inside or {}).get('hitCls')}")
        want_btn = {k: {"clipped": False, "overflowRight": False, "topIsSelf": True} for k in btn_tops}
        C.check("F3-b 三顆操作鈕在 58px 固定欄內都沒被裁、沒溢出、elementFromPoint 仍是自己",
                btn_tops == want_btn, btn_tops, want_btn)
        await close_page(page)

        # ══════════ MUT-F1：資料表改字 → 兩個消費端都要跟著變 ══════════
        logp("\n--- MUT-F1 真突變：改資料表一條 hint/desc（兩個消費端各驗一次）---")
        mutF1 = {}
        try:
            patch_file(SHORTCUTS_JS, 'hint: "循環",', 'hint: "循環ZZ",')
            patch_file(
                SHORTCUTS_JS,
                'desc: "⏱ 工具列開著時：切換循環試聽（關閉工具列後 P 恢復單次試聽）",',
                'desc: "⏱ 工具列開著時：切換循環試聽ZZ（關閉工具列後 P 恢復單次試聽）",',
            )
            pg, _ = await open_pod(browser, ws, sessions)
            await open_modal_via_button(pg)
            mm = await C.js(pg, JS_MODAL)
            await click_selector(pg, "#shortcuts-close")
            await open_time_toolbar(pg, nth=1)
            hh = await C.js(pg, JS_HINTS)
            redF1_modal, _ = raw_eq("MUT-F1-RED modal 的 desc 跟著資料表變了",
                                    "切換循環試聽ZZ" in (mm.get("html") or ""), True)
            redF1_hint, _ = raw_eq("MUT-F1-RED 提示列的 hint 跟著資料表變了",
                                   "循環ZZ" in (hh.get("html") or ""), True)
            redF1_frozen, _ = raw_eq("MUT-F1-RED 提示列已不等於凍結字串（F1-b 會變紅）",
                                     hh.get("html") == FROZEN_HINT, False)
            mutF1["red"] = {"modal": redF1_modal, "hint": redF1_hint, "frozenRed": redF1_frozen}
            await close_page(pg)
        finally:
            patch_file(SHORTCUTS_JS, 'hint: "循環ZZ",', 'hint: "循環",')
            patch_file(
                SHORTCUTS_JS,
                'desc: "⏱ 工具列開著時：切換循環試聽ZZ（關閉工具列後 P 恢復單次試聽）",',
                'desc: "⏱ 工具列開著時：切換循環試聽（關閉工具列後 P 恢復單次試聽）",',
            )
        pg, _ = await open_pod(browser, ws, sessions)
        await open_time_toolbar(pg, nth=1)
        hg = await C.js(pg, JS_HINTS)
        greenF1, _ = raw_eq("MUT-F1-GREEN 還原後提示列回到凍結字串", hg.get("html"), FROZEN_HINT)
        await close_page(pg)
        C.check("MUT-F1 真突變成立：改一條資料 → modal 與提示列兩個消費端同時變（RED），還原→綠",
                all(mutF1.get("red", {}).values()) and greenF1,
                {"red": mutF1.get("red"), "green": greenF1},
                {"red": {"modal": True, "hint": True, "frozenRed": True}, "green": True})

        # ══════════ MUT-F3A：.card 末欄換回 auto ══════════
        logp("\n--- MUT-F3A 真突變：.card 的 --card-actions-w 換回 auto（單機集）---")
        mutF3A = {}
        try:
            patch_file(APP_CSS, "  --card-actions-w: 58px;", "  --card-actions-w: auto;")
            pg, _ = await open_pod(browser, ws, sessions)
            gm = await C.js(pg, JS_CARD_GEOM)
            redA_spread, _ = raw_eq("MUT-F3A-RED 右緣參差回到 18px", geom_spread(gm), 18.0)
            redA_widths, _ = raw_eq("MUT-F3A-RED 操作欄寬度回到兩種（28／46）",
                                    gm.get("actWs"), [28.0, 46.0])
            mutF3A["red"] = {"spread": redA_spread, "widths": redA_widths, "actWs": gm.get("actWs")}
            await close_page(pg)
        finally:
            patch_file(APP_CSS, "  --card-actions-w: auto;", "  --card-actions-w: 58px;")
        pg, _ = await open_pod(browser, ws, sessions)
        gg = await C.js(pg, JS_CARD_GEOM)
        greenA, _ = raw_eq("MUT-F3A-GREEN 還原後參差回到 0", geom_spread(gg), 0)
        await close_page(pg)
        C.check("MUT-F3A 真突變成立：末欄 auto → 參差 18px（RED），固定寬 → 0（GREEN）",
                redA_spread and redA_widths and greenA,
                {"red": mutF3A.get("red"), "green": geom_spread(gg)},
                {"red": {"spread": True, "widths": True, "actWs": [28.0, 46.0]}, "green": 0})

        # ══════════ F1-c：資料表清空 → 兩個消費端都要看得到錯誤 ══════════
        logp("\n--- F1-c 失敗路徑不靜默（資料表為空）---")
        try:
            patch_file(
                SHORTCUTS_JS,
                "export const SHORTCUTS = [",
                "export const SHORTCUTS = [];\nconst _SHORTCUTS_EMPTIED_BY_WALKTHROUGH = [",
            )
            pg, _ = await open_pod(browser, ws, sessions)
            await open_modal_via_button(pg)
            mm = await C.js(pg, JS_MODAL)
            await click_selector(pg, "#shortcuts-close")
            await open_time_toolbar(pg, nth=1)
            hh = await C.js(pg, JS_HINTS)
            got_c = {
                "modalOpen": mm.get("open"),
                "errRows": mm.get("errRows"),
                "errTextNonEmpty": bool((mm.get("errText") or "").strip()),
                "notBlank": bool((mm.get("html") or "").strip()),
                "hintErrRows": hh.get("errRows"),
                "hintNotBlank": bool((hh.get("html") or "").strip()),
            }
            want_c = {"modalOpen": True, "errRows": 1, "errTextNonEmpty": True,
                      "notBlank": True, "hintErrRows": 1, "hintNotBlank": True}
            C.check("F1-c 資料表為空 → modal 與提示列各留可見錯誤文字（不是空白）",
                    got_c == want_c, got_c, want_c)
            await close_page(pg)
        finally:
            patch_file(
                SHORTCUTS_JS,
                "export const SHORTCUTS = [];\nconst _SHORTCUTS_EMPTIED_BY_WALKTHROUGH = [",
                "export const SHORTCUTS = [",
            )

        stop_server(server)
        server = None

        # ══════════ 第二輪：雙機集（F2／F3 雙機） ══════════
        logp("\n--- 第二輪：episode.yaml 加 cameras.b，重起伺服器 ---")
        shutil.copyfile(CAM_A_MP4, CAM_B_MP4)
        EP_YAML.write_text(base_yaml + YAML_CAMERAS, encoding="utf-8")
        server = start_server()
        page, ready = await open_pod(browser, ws, sessions)
        cam_ready = await C.js(
            page,
            "(() => { const c = document.querySelectorAll('#cards-list .card .cam-b-btn');"
            " return { pills: c.length, hasCamClass:"
            " document.querySelectorAll('#cards-list .card.card-has-cam').length }; })()",
        )
        C.check("F2-fixture 雙機集：每張卡都有 A/B 膠囊且帶 card-has-cam",
                bool(ready) and cam_ready["pills"] > 0
                and cam_ready["pills"] == cam_ready["hasCamClass"],
                cam_ready, {"pills": ">0", "hasCamClass": "== pills"})

        logp("\n--- F2 鏡頭 A/B 鈕 tooltip 四態 ---")
        # 四態靠純點擊造出：點第 1 卡 B（explicit b）→ 第 2 卡繼承 b
        #                  點第 3 卡 A（explicit a）→ 第 4 卡繼承 a
        ok_b1, top_b1 = await click_cam(page, 1, "b")
        ok_a3, top_a3 = await click_cam(page, 3, "a")
        C.check("F2-a.0 A/B 鈕點得到（elementFromPoint 最上層是它自己）",
                bool(ok_b1 and top_b1.get("hit") and ok_a3 and top_a3.get("hit")),
                {"b1": top_b1, "a3": top_a3}, True)
        cam = await C.js(page, JS_CAM)
        states = [
            ("explicit b（第 1 卡，自己標的 B）", 0, T_A_SWITCH, T_B_EXPLICIT),
            ("繼承 b（第 2 卡，沿用前一張）", 1, T_A_SWITCH, T_B_INHERIT),
            ("explicit a（第 3 卡，自己標的 A）", 2, T_A_EXPLICIT, T_B_SWITCH),
            ("繼承 a（第 4 卡，沿用前一張）", 3, T_A_INHERIT, T_B_SWITCH),
        ]
        f2_titles = {}
        for label, i, wa, wb in states:
            got = {"aTitle": cam[i]["aTitle"], "bTitle": cam[i]["bTitle"]}
            want = {"aTitle": wa, "bTitle": wb}
            f2_titles[label] = got
            C.check(f"F2-a {label}：A/B 兩顆 tooltip 都說對", got == want, got, want)
        got_act = [{"a": c["aActive"], "b": c["bActive"]} for c in cam]
        want_act = [{"a": False, "b": True}, {"a": False, "b": True},
                    {"a": True, "b": False}, {"a": True, "b": False}]
        C.check("F2-b 點 A/B 仍真的切鏡頭（active 高亮跟著 carry-forward 走）",
                got_act == want_act, got_act, want_act)

        logp("\n--- F3 卡片操作欄固定寬（雙機集：.card.card-has-cam 是另一條規則）---")
        g3 = await C.js(page, JS_CARD_GEOM)
        got_f3d = {"nReview": g3.get("nReview"), "txtRs": g3.get("txtRs"),
                   "actWs": g3.get("actWs"), "spread": geom_spread(g3)}
        C.check("F3-a3 雙機集：全卡右緣同一個 x（參差 0px）、操作欄恆 58px",
                g3.get("nReview") >= 1 and len(g3.get("txtRs", [])) == 1
                and g3.get("actWs") == [58.0] and geom_spread(g3) == 0,
                got_f3d, {"nReview": ">=1", "txtRs": "單一值", "actWs": [58.0], "spread": 0})

        errs2 = C.console_errors(page)
        C.check("F2/F3 雙機輪：無未捕捉的 console 例外", errs2 == [], errs2, [])
        await close_page(page)

        # ══════════ MUT-F2：還原成舊三元 ══════════
        logp("\n--- MUT-F2 真突變：camBtnTitle 兩個呼叫端還原成舊三元 ---")
        mutF2 = {}
        try:
            patch_file(APP_JS, MUT_F2_A_NEW, MUT_F2_A_OLD)
            patch_file(APP_JS, MUT_F2_B_NEW, MUT_F2_B_OLD)
            pg, _ = await open_pod(browser, ws, sessions)
            await click_cam(pg, 1, "b")
            await click_cam(pg, 3, "a")
            cm = await C.js(pg, JS_CAM)
            redF2_a1, _ = raw_eq("MUT-F2-RED 第 1 卡（explicit b）的 A 鈕又謊稱自己是目前鏡頭",
                                 cm[0]["aTitle"], T_A_EXPLICIT)
            redF2_b3, _ = raw_eq("MUT-F2-RED 第 3 卡（explicit a）的 B 鈕又說「切到 B」帶 explicit 字樣",
                                 cm[2]["bTitle"], "鏡頭 B：切到 B 鏡頭（已 explicit 標記）")
            redF2_a2, _ = raw_eq("MUT-F2-RED 第 2 卡（繼承 b）的 A 鈕又謊稱目前鏡頭是 A",
                                 cm[1]["aTitle"], T_A_INHERIT)
            mutF2["red"] = {"a1": redF2_a1, "b3": redF2_b3, "a2": redF2_a2}
            await close_page(pg)
        finally:
            patch_file(APP_JS, MUT_F2_A_OLD, MUT_F2_A_NEW)
            patch_file(APP_JS, MUT_F2_B_OLD, MUT_F2_B_NEW)
        pg, _ = await open_pod(browser, ws, sessions)
        await click_cam(pg, 1, "b")
        await click_cam(pg, 3, "a")
        cg = await C.js(pg, JS_CAM)
        greenF2, _ = raw_eq("MUT-F2-GREEN 還原後第 1 卡 A 鈕改說「切到 A 鏡頭」",
                            cg[0]["aTitle"], T_A_SWITCH)
        await close_page(pg)
        C.check("MUT-F2 真突變成立：舊三元 → 四態說謊（RED），camBtnTitle → 說對（GREEN）",
                all(mutF2.get("red", {}).values()) and greenF2,
                {"red": mutF2.get("red"), "green": greenF2},
                {"red": {"a1": True, "b3": True, "a2": True}, "green": True})

        # ══════════ MUT-F3B：.card.card-has-cam 末欄換回 auto ══════════
        logp("\n--- MUT-F3B 真突變：.card.card-has-cam 末欄換回 auto（雙機集那一半）---")
        try:
            patch_file(APP_CSS,
                       "  grid-template-columns: 18px 66px 1fr auto var(--card-actions-w);",
                       "  grid-template-columns: 18px 66px 1fr auto auto;")
            pg, _ = await open_pod(browser, ws, sessions)
            gm = await C.js(pg, JS_CARD_GEOM)
            redB_spread, spreadB = raw_eq("MUT-F3B-RED 雙機集右緣參差回來（>0）",
                                          geom_spread(gm) > 0, True)
            redB_widths, widthsB = raw_eq("MUT-F3B-RED 雙機集操作欄寬度回到多種",
                                          len(gm.get("actWs", [])) > 1, True)
            await close_page(pg)
        finally:
            patch_file(APP_CSS,
                       "  grid-template-columns: 18px 66px 1fr auto auto;",
                       "  grid-template-columns: 18px 66px 1fr auto var(--card-actions-w);")
        pg, _ = await open_pod(browser, ws, sessions)
        gg2 = await C.js(pg, JS_CARD_GEOM)
        greenB, _ = raw_eq("MUT-F3B-GREEN 還原後雙機集參差回到 0", geom_spread(gg2), 0)
        await close_page(pg)
        C.check("MUT-F3B 真突變成立：card-has-cam 末欄 auto → 參差回來（RED），吃固定寬 → 0（GREEN）",
                redB_spread and redB_widths and greenB,
                {"redSpread": spreadB, "redWidths": widthsB, "green": geom_spread(gg2)},
                {"redSpread": True, "redWidths": True, "green": 0})

        stop_server(server)
        server = None

        bad = C.summary()
        for line in C.results_as_dict():
            log_lines.append(f"{line['status'].upper()}\t{line['name']}")
        RESULT_JSON.write_text(
            json.dumps({"mode": "f-tier-editor-small-defects", "results": C.results_as_dict()},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        LOG_TXT.write_text("\n".join(log_lines), encoding="utf-8")
        return 1 if bad else 0
    finally:
        stop_server(server)
        # 沙盒與原始碼全部還原成跑之前的樣子（突變若中途拋例外也不留痕）
        EP_YAML.write_text(base_yaml, encoding="utf-8")
        SRT.write_text(base_srt, encoding="utf-8")
        if CAM_B_MP4.exists():
            CAM_B_MP4.unlink()
        for p, text in src_snapshot.items():
            if p.read_text(encoding="utf-8") != text:
                p.write_text(text, encoding="utf-8")
                print(f"[還原] {p.name} 被突變留下的改動已復原", flush=True)
        try:
            await ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
