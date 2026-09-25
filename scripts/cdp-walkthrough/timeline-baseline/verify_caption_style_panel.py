#!/usr/bin/env python3
"""D2-FE 驗收走查：主編輯器「字幕樣式」面板是否真的接通 state → 預覽 → episode.yaml。

D2 之前，後端 build_style_string 讀得到 9 個樣式參數，但 UI 只開放 font_size
（字級 ± 鈕），其餘 8 個只能手改 yaml。本檔驗的是這條鏈：
  面板欄位 → commitCaptionStyleField → activeSubtitleStyle() 物件
  → renderCropInfo/applyCaptionStyle → #caption-overlay 實測 CSS
  → 「完成並儲存」→ episode.yaml → 重啟後讀回原值（round-trip）

斷言紀律（README／MUTATIONS.md）：
  - 每個 ✓ 綁布林斷言 ok=實得==期待，不印值不斷言。
  - 可點元素驗 elementFromPoint 最上層真的是它。
  - 開頭先驗版本指紋（面板 DOM 必須真的在服務中的頁面上），避免測到未同步的舊碼。
  - 四態各驗一次：empty（沒樣式物件）／error（非法輸入）／success（寫回＋預覽）／
    loading 以 empty 態涵蓋（面板打開時樣式還沒到＝欄位 disabled）。
  - 兩項真突變：MUT-A 拿掉 api.js 存檔白名單的 margin_v → round-trip 變紅 → 還原變綠；
    MUT-B 拿掉 commitCaptionStyleField 的非法值 return → error 態變紅 → 還原變綠。

round-trip 要重讀 episode.yaml，而 Episode 的 cfg 只在建構時讀一次，所以本檔自己
起／停 serve_podcast.py（:8795），每次存檔後重起一次；跑完把 episode.yaml 還原。

前提：headless Chrome CDP（預設 :9522）已啟動；:8795 未被占用。
跑法：CDP_PORT=9522 /usr/bin/env python3 -u verify_caption_style_panel.py
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import cdp_common as C

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent.parent
APP_JS = REPO_ROOT / "podcast_toolkit" / "web" / "static" / "app.js"
API_JS = REPO_ROOT / "podcast_toolkit" / "web" / "static" / "api.js"
INDEX_HTML = REPO_ROOT / "podcast_toolkit" / "web" / "static" / "index.html"
SANDBOX_ROOT = Path("/private/tmp/pt-timeline-baseline")
EP_YAML = SANDBOX_ROOT / "episode" / "20260601 時間軸基準集" / "episode.yaml"

CDP_PORT = int(os.environ.get("CDP_PORT", "9522"))
POD_PORT = 8795
POD = f"http://127.0.0.1:{POD_PORT}/?pthook=1"

RESULT_JSON = HERE / "verify_caption_style_panel_result.json"
LOG_TXT = HERE / "verify_caption_style_panel_log.txt"

OUT_H = 1080  # encode.resolution 預設 1920x1080（episode_io.py:318）

# defaults.yaml 的 subtitle_style（走查自己抄一份當對照，不呼叫產品碼）
DEFAULTS = {
    "font_name": "Hiragino Sans GB", "font_size": 60, "bold": 1,
    "primary_colour": "&H00FFFFFF", "outline_colour": "&H00000000",
    "border_style": 1, "outline": 2, "shadow": 1, "margin_v": 100,
}

# 目標值：每一項都刻意偏離 defaults，否則「沒生效」也會看起來對
TARGET = {
    "font_name": "PingFang TC",
    "primary_colour": "&H0000FFFF",   # BGR 00FFFF → #ffff00 黃
    "outline_colour": "&H00FF0000",   # BGR FF0000 → #0000ff 藍
    "bold": 0,
    "border_style": 3,
    "outline": 4,
    "shadow": 0,
    "alignment": 6,
    "margin_v": 200,
}


def ass_to_rgb(ass: str) -> str:
    bgr = ass[-6:]
    b, g, r = int(bgr[0:2], 16), int(bgr[2:4], 16), int(bgr[4:6], 16)
    return f"rgb({r}, {g}, {b})"


def ass_to_hex(ass: str) -> str:
    bgr = ass[-6:]
    return f"#{bgr[4:6]}{bgr[2:4]}{bgr[0:2]}".lower()


def parse_pct(s):
    return float(str(s).replace("%", "")) if s and str(s).endswith("%") else None


# ── serve_podcast 生命週期（抄 verify_podcast_caption_style.py）────────────
def port_busy(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
        return True
    except urllib.error.URLError:
        return False
    except Exception:
        return True


def start_server():
    log = open(SANDBOX_ROOT / "server-capstyle-panel.log", "ab")
    p = subprocess.Popen(
        [sys.executable, str(HERE / "serve_podcast.py")], stdout=log, stderr=log
    )
    for _ in range(120):
        if p.poll() is not None:
            raise RuntimeError("serve_podcast 啟動即退出，看 server-capstyle-panel.log")
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


def read_yaml_style():
    """直接從磁碟讀 episode.yaml 的 subtitle_style（不透過產品碼的讀取路徑）。"""
    import yaml
    data = yaml.safe_load(EP_YAML.read_text(encoding="utf-8")) or {}
    return data.get("subtitle_style")


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


async def top_hits_button(page, x, y, selector):
    """(x,y) 最上層元素是否落在 selector 之內（含子節點）。
    按鈕把標籤包在 <span> 裡時 elementFromPoint 會回那個 span，點擊照樣冒泡到按鈕；
    要擋的是「別的疊層蓋在上面」，所以判準是 closest() 命中，不是 id 全等。"""
    return await C.js(
        page,
        f"""(() => {{
          const el = document.elementFromPoint({x}, {y});
          if (!el) return null;
          return {{ tag: el.tagName, id: el.id || null,
                    within: !!el.closest({json.dumps(selector)}) }};
        }})()""",
    )


async def close_page(page):
    if page is None:
        return
    try:
        await page.send("Page.close")
    except Exception:
        pass
    await asyncio.sleep(0.2)


async def open_pod(browser, ws, sessions):
    page = await C.open_page(browser, ws, sessions, POD, settle=2.0)
    ready = await wait_for(
        page,
        "(() => { const o = document.querySelector('#caption-overlay');"
        " return !!(o && o.style.fontSize); })()",
    )
    return page, ready


async def seek_to_caption(page):
    """seek 到第一句字幕的中點，讓 overlay 真的長出 .caption-line（才量得到底色塊）。"""
    vr = await C.js(
        page,
        "(() => { const v=document.querySelector('#video');"
        " return v ? {readyState:v.readyState, seekableLen:v.seekable.length} : null; })()",
    )
    if not (vr and vr["readyState"] >= 1 and vr["seekableLen"] > 0):
        return None, vr
    t = await C.js(
        page,
        "(() => { const b = document.querySelector('#card-timeline .tl-block');"
        " if (!b) return null; const s=Number(b.dataset.start), e=Number(b.dataset.end);"
        " return (s+e)/2; })()",
    )
    if t is None:
        return None, vr
    await C.js(page, f"(() => {{ document.querySelector('#video').currentTime = {t}; }})()")
    await wait_for(page, "document.querySelectorAll('#caption-overlay .caption-line').length > 0")
    return t, vr


async def open_panels(page):
    """真的點開「出片設定」<details>，再真的點開字幕樣式面板（含疊層驗證）。"""
    summ = await C.element_rect_center(page, ".framing-panel .framing-summary")
    await C.mouse_click(page, summ["cx"], summ["cy"])
    await asyncio.sleep(0.35)
    opened = await C.js(page, "!!document.querySelector('.framing-panel')?.open")
    btn = await C.element_rect_center(page, "#cap-style-btn")
    top_el = await top_hits_button(page, btn["cx"], btn["cy"], "#cap-style-btn")
    await C.mouse_click(page, btn["cx"], btn["cy"])
    await asyncio.sleep(0.35)
    panel = await C.js(
        page,
        "(() => { const p=document.querySelector('#cap-style-panel');"
        " const b=document.querySelector('#cap-style-btn');"
        " return {open: p ? !p.classList.contains('hidden') : null,"
        " aria: b ? b.getAttribute('aria-expanded') : null}; })()",
    )
    return opened, top_el, panel


async def set_field(page, sel, value, kind):
    """用真事件驅動既有 listener（colour 走 input、其餘走 change），不繞過 commit。"""
    if kind == "bool":
        expr = (
            f"(() => {{ const el=document.querySelector('{sel}');"
            f" el.checked={'true' if value else 'false'};"
            " el.dispatchEvent(new Event('change', {bubbles:true})); return true; })()"
        )
    else:
        evt = "input" if kind == "colour" else "change"
        expr = (
            f"(() => {{ const el=document.querySelector('{sel}');"
            f" el.value={json.dumps(str(value))};"
            f" el.dispatchEvent(new Event('{evt}', {{bubbles:true}}));"
            " return true; })()"
        )
    await C.js(page, expr)
    await asyncio.sleep(0.15)


async def type_into(page, sel, text):
    """真鍵盤輸入（number input 打 '1e' 才會產生 badInput，程式化賦值造不出來）。"""
    await C.js(
        page,
        f"(() => {{ const el=document.querySelector('{sel}'); el.value=''; el.focus();"
        " return document.activeElement === el; })()",
    )
    for ch in text:
        for typ in ("keyDown", "char", "keyUp"):
            p = {"type": typ, "key": ch}
            if typ != "keyUp":
                p.update({"text": ch, "unmodifiedText": ch})
            await page.send("Input.dispatchKeyEvent", p)
        await asyncio.sleep(0.03)
    await asyncio.sleep(0.1)
    state = await C.js(
        page,
        f"(() => {{ const el=document.querySelector('{sel}');"
        " return {value: el.value, badInput: el.validity.badInput}; })()",
    )
    # blur 才觸發 change（使用者改過的欄位失焦即 commit）
    await C.js(page, f"(() => {{ document.querySelector('{sel}').blur(); return true; }})()")
    await asyncio.sleep(0.2)
    return state


async def measure(page):
    return await C.js(
        page,
        """(() => {
          const o = document.querySelector('#caption-overlay');
          const line = o.querySelector('.caption-line');
          const cs = getComputedStyle(o);
          const ls = line ? getComputedStyle(line) : null;
          return {
            lineCount: o.querySelectorAll('.caption-line').length,
            inline: { fontSize:o.style.fontSize, top:o.style.top,
                      bottom:o.style.bottom, transform:o.style.transform },
            computed: { fontFamily:cs.fontFamily, fontWeight:cs.fontWeight,
                        color:cs.color, textShadow:cs.textShadow },
            line: ls ? { background:ls.backgroundColor, textShadow:ls.textShadow } : null,
          };
        })()""",
    )


async def panel_state(page):
    return await C.js(
        page,
        """(() => {
          const grid = document.querySelector('#cap-style-grid');
          const empty = document.querySelector('#cap-style-empty');
          const err = document.querySelector('#cap-style-err');
          const mv = document.querySelector('#cap-margin-v');
          return {
            gridHidden: grid ? grid.classList.contains('hidden') : null,
            emptyHidden: empty ? empty.classList.contains('hidden') : null,
            errHidden: err ? err.classList.contains('hidden') : null,
            errText: err ? err.textContent : null,
            marginDisabled: mv ? mv.disabled : null,
            invalidCount: document.querySelectorAll('#cap-style-panel .invalid').length,
          };
        })()""",
    )


async def read_fields(page):
    """從面板欄位讀回值（round-trip 的「讀回」端＝使用者看得到的那一份）。"""
    return await C.js(
        page,
        """(() => {
          const g = (id) => document.getElementById(id);
          return {
            font_name: g('cap-font-name').value,
            primary_colour: g('cap-primary-colour').value,
            outline_colour: g('cap-outline-colour').value,
            bold: g('cap-bold').checked ? 1 : 0,
            border_style: Number(g('cap-border-style').value),
            outline: Number(g('cap-outline').value),
            shadow: Number(g('cap-shadow').value),
            alignment: Number(g('cap-alignment').value),
            margin_v: Number(g('cap-margin-v').value),
          };
        })()""",
    )


async def apply_target(page, overrides=None):
    """把 TARGET（可覆寫）逐欄位打進面板。回傳實際套用的值。"""
    vals = dict(TARGET)
    vals.update(overrides or {})
    await set_field(page, "#cap-font-name", vals["font_name"], "text")
    await set_field(page, "#cap-primary-colour", ass_to_hex(vals["primary_colour"]), "colour")
    await set_field(page, "#cap-outline-colour", ass_to_hex(vals["outline_colour"]), "colour")
    await set_field(page, "#cap-bold", vals["bold"], "bool")
    await set_field(page, "#cap-outline", vals["outline"], "num")
    await set_field(page, "#cap-shadow", vals["shadow"], "num")
    await set_field(page, "#cap-border-style", vals["border_style"], "select")
    await set_field(page, "#cap-alignment", vals["alignment"], "select")
    await set_field(page, "#cap-margin-v", vals["margin_v"], "num")
    return vals


async def save_and_wait(page):
    """真的點「完成並儲存」，等磁碟上的 episode.yaml 真的變了（不看按鈕文案）。"""
    before = EP_YAML.read_text(encoding="utf-8")
    btn = await C.element_rect_center(page, "#save-btn")
    top_el = await top_hits_button(page, btn["cx"], btn["cy"], "#save-btn")
    await C.mouse_click(page, btn["cx"], btn["cy"])
    for _ in range(80):
        await asyncio.sleep(0.25)
        if EP_YAML.read_text(encoding="utf-8") != before:
            await asyncio.sleep(0.6)  # 等 loadEpisodeState 回來
            return True, top_el
    return False, top_el


# ── 突變點（產品碼字串，必須逐字命中）──────────────────────────────────────
MUT_A_OLD = '  "margin_v",\n'
MUT_A_NEW = '  // "margin_v",  // MUT-A\n'
MUT_B_OLD = (
    '      setCaptionStyleError(`${f.label}：數值不合法，這次改動沒有套用`, el);\n'
    "      return;\n"
)
MUT_B_NEW = (
    '      setCaptionStyleError(`${f.label}：數值不合法，這次改動沒有套用`, el);\n'
    "      // MUT-B：拿掉守衛，讓非法值照樣寫進 state\n"
)


async def main():
    if port_busy(POD_PORT):
        print(f"[ABORT] :{POD_PORT} 已被占用，本檔要自己管理 serve_podcast，請先停掉", flush=True)
        return 2

    base_yaml = EP_YAML.read_text(encoding="utf-8")
    if "subtitle_style" in base_yaml:
        print("[ABORT] 沙盒 episode.yaml 已含 subtitle_style，先還原再跑", flush=True)
        return 2

    log_lines = []

    def logp(s=""):
        print(s, flush=True)
        log_lines.append(str(s))

    ws_url = get_cdp_ws_url(CDP_PORT)
    browser, sessions, ws = await C.connect(ws_url)
    server = None
    try:
        logp("=" * 70)
        logp(f"D2-FE 字幕樣式面板走查  URL={POD}  CDP={CDP_PORT}")
        logp("=" * 70)

        server = start_server()
        page, ready = await open_pod(browser, ws, sessions)

        # ══════════ V0 版本指紋 ══════════
        logp("\n--- V0 版本指紋（避免測到未同步的舊碼）---")
        C.check("V0.0 podcast 編輯器已載入且 overlay 已套上 inline 樣式", bool(ready), ready, True)
        fp = {
            "setupCaptionStyle": "function setupCaptionStyle()" in APP_JS.read_text(encoding="utf-8"),
            "SUBTITLE_STYLE_KEYS": "const SUBTITLE_STYLE_KEYS = [" in API_JS.read_text(encoding="utf-8"),
            "panelInHtml": 'id="cap-style-panel"' in INDEX_HTML.read_text(encoding="utf-8"),
        }
        want_fp = {"setupCaptionStyle": True, "SUBTITLE_STYLE_KEYS": True, "panelInHtml": True}
        C.check("V0.1 原始碼指紋：app.js/api.js/index.html 都是 D2-FE 後的版本", fp == want_fp, fp, want_fp)
        dom_fp = await C.js(
            page,
            "(() => ({panel: !!document.querySelector('#cap-style-panel'),"
            " btn: !!document.querySelector('#cap-style-btn'),"
            " hook: typeof window.__ptSetCaptionStyle}))()",
        )
        want_dom = {"panel": True, "btn": True, "hook": "function"}
        C.check("V0.2 服務中的頁面真的有面板 DOM 與 pthook（來源已同步）",
                dom_fp == want_dom, dom_fp, want_dom)

        t, vr = await seek_to_caption(page)
        prereq = t is not None
        C.check("V0.3 seek 前提：#video readyState>=1 且 seekable.length>0（底色塊要有字才量得到）",
                prereq, vr, "readyState>=1 且 seekableLen>0")
        if not prereq:
            C.skip("T2.*／T6.* 後續斷言", "seek 前提不成立（環境不可 seek），不假裝通過")
            C.summary()
            return 1

        # ══════════ T1 開面板（真點擊＋疊層驗證）══════════
        logp("\n--- T1 打開字幕樣式面板 ---")
        det_open, top_el, ps = await open_panels(page)
        C.check("T1.0 點「出片設定」摘要列 → 面板展開（字幕樣式鈕才摸得到）",
                det_open is True, det_open, True)
        C.check("T1.1 「字幕樣式」鈕未被疊層遮擋（elementFromPoint 最上層落在按鈕內）",
                bool(top_el and top_el.get("within")), top_el, {"within": True})
        C.check("T1.2 真點擊 → 面板展開且 aria-expanded=true",
                ps == {"open": True, "aria": "true"}, ps, {"open": True, "aria": "true"})
        st0 = await panel_state(page)
        C.check("T1.3 已開集 → success 態：欄位可用、empty 說明收起、無錯誤",
                (st0["gridHidden"], st0["emptyHidden"], st0["errHidden"], st0["marginDisabled"])
                == (False, True, True, False), st0,
                {"gridHidden": False, "emptyHidden": True, "errHidden": True, "marginDisabled": False})
        fields0 = await read_fields(page)
        want0 = {
            "font_name": DEFAULTS["font_name"],
            "primary_colour": ass_to_hex(DEFAULTS["primary_colour"]),
            "outline_colour": ass_to_hex(DEFAULTS["outline_colour"]),
            "bold": DEFAULTS["bold"], "border_style": DEFAULTS["border_style"],
            "outline": DEFAULTS["outline"], "shadow": DEFAULTS["shadow"],
            "alignment": 2, "margin_v": DEFAULTS["margin_v"],
        }
        C.check("T1.4 初始值＝defaults.yaml 的 subtitle_style（alignment 未設 → 保底 2 底部）",
                fields0 == want0, fields0, want0)

        # ══════════ T3 未儲存計數（改之前先取基準）══════════
        badge0 = await C.js(
            page,
            "(() => { const b=document.querySelector('#unsaved-badge');"
            " return {hidden: b.classList.contains('hidden'),"
            " n: document.querySelector('#unsaved-count').textContent}; })()",
        )
        C.check("T3.0 改動前：未儲存徽章是收起的（基準乾淨）",
                badge0["hidden"] is True, badge0, {"hidden": True})

        # ══════════ T2 success：逐參數改 → 量預覽實測 CSS ══════════
        logp("\n--- T2 success 態：面板改值 → #caption-overlay 實測 CSS ---")
        await set_field(page, "#cap-font-name", TARGET["font_name"], "text")
        badge1 = await C.js(
            page,
            "(() => { const b=document.querySelector('#unsaved-badge');"
            " return {hidden: b.classList.contains('hidden'),"
            " n: Number(document.querySelector('#unsaved-count').textContent)}; })()",
        )
        C.check("T3.1 改一個欄位 → 未儲存徽章亮起且計數 ≥1（進 outputDirty，不是靜默改）",
                badge1["hidden"] is False and badge1["n"] >= 1, badge1,
                {"hidden": False, "n": ">=1"})

        await set_field(page, "#cap-primary-colour", ass_to_hex(TARGET["primary_colour"]), "colour")
        await set_field(page, "#cap-outline-colour", ass_to_hex(TARGET["outline_colour"]), "colour")
        await set_field(page, "#cap-bold", TARGET["bold"], "bool")
        await set_field(page, "#cap-outline", TARGET["outline"], "num")
        await set_field(page, "#cap-shadow", TARGET["shadow"], "num")
        m1 = await measure(page)

        C.check("T2.1 font_name → 預覽字型堆疊第一順位換成 PingFang TC",
                m1["computed"]["fontFamily"].startswith('"PingFang TC"'),
                m1["computed"]["fontFamily"][:24], '"PingFang TC", …')
        want_color = ass_to_rgb(TARGET["primary_colour"])
        C.check("T2.2 primary_colour → 預覽文字色變黃（面板 hex → ASS → CSS 兩次轉換沒走樣）",
                m1["computed"]["color"] == want_color, m1["computed"]["color"], want_color)
        C.check("T2.3 取消粗體 → fontWeight 400（defaults 是 1，證明真的讀面板）",
                m1["computed"]["fontWeight"] == "400", m1["computed"]["fontWeight"], "400")
        want_outline = ass_to_rgb(TARGET["outline_colour"])
        sh1 = m1["computed"]["textShadow"] or ""
        C.check("T2.4 outline=4＋outline_colour → 12 向描邊皆為藍",
                sh1.count(want_outline) == 12, sh1.count(want_outline), 12)
        C.check("T2.5 shadow=0 → 描邊之外沒有額外投影",
                "rgba(0, 0, 0, 0.9)" not in sh1, "rgba(0, 0, 0, 0.9)" in sh1, False)

        await set_field(page, "#cap-border-style", TARGET["border_style"], "select")
        m2 = await measure(page)
        C.check("T2.6 border_style=3 → 字幕行帶不透明底色塊（底色＝outline_colour 藍）",
                m2["line"] is not None and m2["line"]["background"] == want_outline,
                m2["line"] and m2["line"]["background"], want_outline)
        C.check("T2.7 底色塊模式取消描邊（textShadow: none）",
                m2["computed"]["textShadow"] == "none", m2["computed"]["textShadow"], "none")

        await set_field(page, "#cap-alignment", TARGET["alignment"], "select")
        await set_field(page, "#cap-margin-v", TARGET["margin_v"], "num")
        m3 = await measure(page)
        want_top = TARGET["margin_v"] / OUT_H * 100
        got_top = parse_pct(m3["inline"]["top"])
        C.check(f"T2.8 alignment=6＋margin_v=200 → 字幕置頂，top＝200/{OUT_H}≈{want_top:.2f}%",
                got_top is not None and abs(got_top - want_top) < 0.02, got_top, round(want_top, 2))
        C.check("T2.9 置頂時不設 bottom／transform（落點只由 top 決定）",
                (m3["inline"]["bottom"], m3["inline"]["transform"]) == ("", ""),
                {"bottom": m3["inline"]["bottom"], "transform": m3["inline"]["transform"]},
                {"bottom": "", "transform": ""})

        state_after = await C.js(page, "window.__ptCaptionStyle()")
        want_state = {**DEFAULTS, **TARGET}
        C.check("T2.10 state 裡九個鍵都被寫回（面板 ↔ 字級 ± 鈕同一份物件，沒開第二套）",
                all(state_after.get(k) == v for k, v in want_state.items()),
                {k: state_after.get(k) for k in sorted(want_state)}, want_state)

        # ══════════ T4 error 態 ══════════
        logp("\n--- T4 error 態：非法輸入不得靜默寫進 state ---")
        await set_field(page, "#cap-margin-v", 5000, "num")  # max=2000 → rangeOverflow
        e1 = await panel_state(page)
        s1 = await C.js(page, "window.__ptCaptionStyle()")
        C.check("T4.1 超出上限（5000>2000）→ 面板顯示錯誤訊息且欄位標紅",
                e1["errHidden"] is False and e1["invalidCount"] == 1 and "邊距" in (e1["errText"] or ""),
                {"errHidden": e1["errHidden"], "invalidCount": e1["invalidCount"],
                 "errText": e1["errText"]},
                {"errHidden": False, "invalidCount": 1, "errText": "含「邊距」"})
        C.check("T4.2 非法值沒有被寫進 state（margin_v 仍是 200，不是 5000 也不是 0）",
                s1.get("margin_v") == TARGET["margin_v"], s1.get("margin_v"), TARGET["margin_v"])

        typed = await type_into(page, "#cap-margin-v", "1e")  # 真鍵盤 → badInput
        if not typed.get("badInput"):
            C.skip("T4.3 打非數字 → badInput 攔阻",
                   f"這版 Chrome 沒產生 badInput（value={typed.get('value')!r}），不假裝通過")
        else:
            e2 = await panel_state(page)
            s2 = await C.js(page, "window.__ptCaptionStyle()")
            C.check("T4.3 真鍵盤打 '1e'（badInput）→ 錯誤訊息出現，且 margin_v 沒被靜默清成 0",
                    e2["errHidden"] is False and s2.get("margin_v") == TARGET["margin_v"],
                    {"errHidden": e2["errHidden"], "margin_v": s2.get("margin_v")},
                    {"errHidden": False, "margin_v": TARGET["margin_v"]})
        await set_field(page, "#cap-margin-v", TARGET["margin_v"], "num")
        e3 = await panel_state(page)
        C.check("T4.4 改回合法值 → 錯誤訊息收起、紅框清掉（錯誤態可恢復）",
                e3["errHidden"] is True and e3["invalidCount"] == 0,
                {"errHidden": e3["errHidden"], "invalidCount": e3["invalidCount"]},
                {"errHidden": True, "invalidCount": 0})

        # ══════════ T5 empty 態 ══════════
        logp("\n--- T5 empty 態：還沒有樣式物件時不給一排空白輸入框 ---")
        saved_style = await C.js(page, "window.__ptCaptionStyle()")
        await C.js(page, "window.__ptSetCaptionStyle(null)")
        await asyncio.sleep(0.2)
        st_empty = await panel_state(page)
        C.check("T5.1 樣式為 null → 欄位區收起、說明顯示、欄位 disabled",
                (st_empty["gridHidden"], st_empty["emptyHidden"], st_empty["marginDisabled"])
                == (True, False, True), st_empty,
                {"gridHidden": True, "emptyHidden": False, "marginDisabled": True})
        await C.js(page, f"window.__ptSetCaptionStyle({json.dumps(saved_style)})")
        await asyncio.sleep(0.2)
        st_back = await panel_state(page)
        C.check("T5.2 樣式回來 → 恢復 success 態（empty 不是單向門）",
                (st_back["gridHidden"], st_back["emptyHidden"], st_back["marginDisabled"])
                == (False, True, False), st_back,
                {"gridHidden": False, "emptyHidden": True, "marginDisabled": False})

        errs = C.console_errors(page)
        C.check("T2–T5 全段互動無 console 例外", errs == [], errs, [])

        # ══════════ T6 round-trip ══════════
        logp("\n--- T6 round-trip：存檔 → episode.yaml → 重啟重載 → 讀回原值 ---")
        saved_ok, save_top = await save_and_wait(page)
        C.check("T6.0 「完成並儲存」鈕未被疊層遮擋（elementFromPoint 最上層落在按鈕內）",
                bool(save_top and save_top.get("within")), save_top, {"within": True})
        C.check("T6.1 點存檔 → 磁碟上的 episode.yaml 真的被改寫（不看按鈕文案）",
                saved_ok is True, saved_ok, True)
        disk = read_yaml_style() or {}
        C.check("T6.2 episode.yaml 的 subtitle_style 含全部 9 個調過的鍵且值正確",
                all(disk.get(k) == v for k, v in TARGET.items()),
                {k: disk.get(k) for k in sorted(TARGET)}, TARGET)
        C.check("T6.3 font_size 沒被面板動到（面板不管字級，± 鈕才管）",
                "font_size" not in disk, list(sorted(disk)), "無 font_size")

        stop_server(server)
        server = start_server()
        page2, ready2 = await open_pod(browser, ws, sessions)
        await seek_to_caption(page2)
        await open_panels(page2)
        fields_back = await read_fields(page2)
        want_back = {
            "font_name": TARGET["font_name"],
            "primary_colour": ass_to_hex(TARGET["primary_colour"]),
            "outline_colour": ass_to_hex(TARGET["outline_colour"]),
            "bold": TARGET["bold"], "border_style": TARGET["border_style"],
            "outline": TARGET["outline"], "shadow": TARGET["shadow"],
            "alignment": TARGET["alignment"], "margin_v": TARGET["margin_v"],
        }
        C.check("T6.4 重啟伺服器＋重載頁面 → 面板欄位讀回同一組值（真 round-trip）",
                bool(ready2) and fields_back == want_back, fields_back, want_back)
        m4 = await measure(page2)
        got_top4 = parse_pct(m4["inline"]["top"])
        C.check("T6.5 重載後預覽也吃到存下來的樣式（顏色＋置頂落點都還在）",
                m4["computed"]["color"] == want_color and got_top4 is not None
                and abs(got_top4 - want_top) < 0.02,
                {"color": m4["computed"]["color"], "top": got_top4},
                {"color": want_color, "top": round(want_top, 2)})
        await close_page(page)
        await close_page(page2)

        # ══════════ MUT-A：api.js 存檔白名單拿掉 margin_v ══════════
        logp("\n--- MUT-A 真突變：api.js 的 SUBTITLE_STYLE_KEYS 拿掉 margin_v ---")
        mutA = {}
        stop_server(server)
        server = None
        patch_file(API_JS, MUT_A_OLD, MUT_A_NEW)
        try:
            server = start_server()
            pr, _ = await open_pod(browser, ws, sessions)
            await seek_to_caption(pr)
            await open_panels(pr)
            # outline 仍在白名單內：它變了才證明「存檔真的落地過」，
            # 否則 margin_v 沒動可能只是根本沒存成功
            await set_field(pr, "#cap-outline", 5, "num")
            await set_field(pr, "#cap-margin-v", 320, "num")
            okr, _ = await save_and_wait(pr)
            dr = read_yaml_style() or {}
            mutA["red_saved"] = okr
            mutA["red_outline"] = dr.get("outline")
            raw_eq("MUT-A-RED 對照：outline 仍在白名單 → 存檔確實落地", dr.get("outline"), 5)
            redA_ok, _ = raw_eq("MUT-A-RED 存檔後 margin_v 應為 320（突變後預期停在 200）",
                                dr.get("margin_v"), 320)
            mutA["red"] = dr.get("margin_v")
            await close_page(pr)
            stop_server(server)
            server = None
        finally:
            patch_file(API_JS, MUT_A_NEW, MUT_A_OLD)
        server = start_server()
        pg, _ = await open_pod(browser, ws, sessions)
        await seek_to_caption(pg)
        await open_panels(pg)
        await set_field(pg, "#cap-margin-v", 320, "num")
        await save_and_wait(pg)
        dg = read_yaml_style() or {}
        greenA_ok, _ = raw_eq("MUT-A-GREEN 還原後 margin_v 存得進去", dg.get("margin_v"), 320)
        await close_page(pg)
        mutA["green"] = dg.get("margin_v")
        C.check("MUT-A 真突變成立：白名單少一鍵 → 面板上改得動但存不進 yaml（RED），還原→存得進（GREEN）",
                (redA_ok is False) and (greenA_ok is True) and mutA.get("red_outline") == 5,
                mutA, {"red": "200（沒存進去）", "red_outline": 5, "green": 320})

        # ══════════ MUT-B：拿掉非法值守衛 ══════════
        logp("\n--- MUT-B 真突變：commitCaptionStyleField 的非法值 return 拿掉 ---")
        mutB = {}
        stop_server(server)
        server = None
        patch_file(APP_JS, MUT_B_OLD, MUT_B_NEW)
        try:
            server = start_server()
            pr2, _ = await open_pod(browser, ws, sessions)
            await seek_to_caption(pr2)
            await open_panels(pr2)
            await set_field(pr2, "#cap-margin-v", 5000, "num")
            sr = await C.js(pr2, "window.__ptCaptionStyle()")
            redB_ok, _ = raw_eq("MUT-B-RED 非法值後 margin_v 應維持 320（突變後預期被寫成 5000）",
                                sr.get("margin_v"), 320)
            mutB["red"] = sr.get("margin_v")
            await close_page(pr2)
            stop_server(server)
            server = None
        finally:
            patch_file(APP_JS, MUT_B_NEW, MUT_B_OLD)
        server = start_server()
        pg2, _ = await open_pod(browser, ws, sessions)
        await seek_to_caption(pg2)
        await open_panels(pg2)
        await set_field(pg2, "#cap-margin-v", 5000, "num")
        sg = await C.js(pg2, "window.__ptCaptionStyle()")
        greenB_ok, _ = raw_eq("MUT-B-GREEN 還原後非法值被擋下，margin_v 維持 320",
                              sg.get("margin_v"), 320)
        mutB["green"] = sg.get("margin_v")
        await close_page(pg2)
        C.check("MUT-B 真突變成立：守衛拿掉 → 非法值寫進 state（RED），還原→擋下（GREEN）",
                (redB_ok is False) and (greenB_ok is True), mutB,
                {"red": 5000, "green": 320})

        stop_server(server)
        server = None

        bad = C.summary()
        for line in C.results_as_dict():
            log_lines.append(f"{line['status'].upper()}\t{line['name']}")
        RESULT_JSON.write_text(
            json.dumps({"mode": "d2-caption-style-panel", "results": C.results_as_dict()},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        LOG_TXT.write_text("\n".join(log_lines), encoding="utf-8")
        return 1 if bad else 0
    finally:
        stop_server(server)
        EP_YAML.write_text(base_yaml, encoding="utf-8")  # 沙盒 yaml 還原成跑之前的樣子
        try:
            await ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
