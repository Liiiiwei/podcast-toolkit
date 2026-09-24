#!/usr/bin/env python3
"""W2 驗收走查：podcast 字幕預覽是否真的吃到 episode.yaml 的 subtitle_style。

W2 之前 podcast 預覽只算字級，顏色／描邊／粗體／底色塊／垂直落點全寫死在 app.css
與 renderCropInfo（「Reels 置中、YT 置底 8%」），改了 yaml 也不會反映 → 預覽 ≠ 成品。
本檔驗的是收斂後的那條鏈：episode.yaml → /api/state → buildSubtitleCss（共用核心）
→ #caption-overlay 的 inline CSS 與落點。

斷言紀律（README／MUTATIONS.md）：
  - 每個 ✓ 綁布林斷言 ok=實得==期待，不印值不斷言。
  - seek 前先斷言 #video readyState>=1 且 seekable.length>0。
  - 可點元素驗 elementFromPoint 最上層真的是它。
  - 開頭先驗版本指紋（app.js 必須是 W2 後的版本），避免測到未同步的舊碼。
  - 兩項真突變：把產品碼改回 W2 前行為 → 對應斷言變紅 → 還原變綠（try/finally）。

樣式變體要換 episode.yaml，而 Episode 的 cfg 只在建構時讀一次，所以本檔自己起／停
serve_podcast.py（:8795），每個變體重起一次；跑完把 episode.yaml 還原成原樣。

前提：headless Chrome CDP（預設 :9522）已啟動；:8795 未被占用。
跑法：CDP_PORT=9522 /usr/bin/env python3 -u verify_podcast_caption_style.py
"""
import asyncio
import json
import os
import re
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
SANDBOX_ROOT = Path("/private/tmp/pt-timeline-baseline")
EP_YAML = SANDBOX_ROOT / "episode" / "20260601 時間軸基準集" / "episode.yaml"

CDP_PORT = int(os.environ.get("CDP_PORT", "9522"))
POD_PORT = 8795
POD = f"http://127.0.0.1:{POD_PORT}/?pthook=1"

RESULT_JSON = HERE / "verify_podcast_caption_style_result.json"
LOG_TXT = HERE / "verify_podcast_caption_style_log.txt"

OUT_H = 1080  # encode.resolution 預設 1920x1080（episode_io.py:318）

# ── 三個樣式變體：刻意都偏離 defaults.yaml，否則「沒生效」也會看起來對 ──────
VARIANTS = {
    "A": {
        "desc": "底部置中 · 描邊（border_style=1）· 非預設顏色與粗細",
        "style": {
            "font_name": "Hiragino Sans GB",
            "font_size": 72,
            "bold": 0,                      # 預設是 1 → 驗得出有沒有吃到
            "primary_colour": "&H0000FFFF",  # BGR 00FFFF → #ffff00 黃
            "outline_colour": "&H000000FF",  # BGR 0000FF → #ff0000 紅
            "border_style": 1,
            "outline": 4,
            "shadow": 0,
            "margin_v": 200,                 # 底距 200/1080 = 18.52%（預設寫死 8%）
        },
    },
    "B": {
        "desc": "頂部置中（alignment=6）· 不透明底色塊（border_style=3）",
        "style": {
            "font_name": "Hiragino Sans GB",
            "font_size": 48,
            "bold": 1,
            "primary_colour": "&H00FFFFFF",
            "outline_colour": "&H00FF0000",  # BGR FF0000 → #0000ff 藍底
            "border_style": 3,
            "outline": 3,
            "shadow": 2,
            "alignment": 6,
            "margin_v": 60,
        },
    },
    "C": {
        "desc": "畫面正中央（alignment=10）· margin_v 正值往下偏移",
        "style": {
            "font_name": "Hiragino Sans GB",
            "font_size": 60,
            "bold": 1,
            "primary_colour": "&H0000FF00",  # BGR 00FF00 → #00ff00 綠
            "outline_colour": "&H00000000",
            "border_style": 1,
            "outline": 2,
            "shadow": 1,
            "alignment": 10,
            "margin_v": 108,                 # 中心往下 10%
        },
    },
}


# ── 期待值換算（走查自己算一份，不呼叫產品碼，才有對照意義）────────────────
def ass_to_rgb(ass: str) -> str:
    """&H00BBGGRR → CSS 的 'rgb(r, g, b)'（Chrome getComputedStyle 的序列化格式）。"""
    m = re.match(r"^&H([0-9a-fA-F]{2})?([0-9a-fA-F]{6})$", ass)
    bgr = m.group(2)
    b, g, r = int(bgr[0:2], 16), int(bgr[2:4], 16), int(bgr[4:6], 16)
    return f"rgb({r}, {g}, {b})"


def expect_font_px(style, wrap_h):
    return max(9.0, style["font_size"] * (wrap_h / OUT_H))


def parse_px(s):
    return float(str(s).replace("px", "")) if s and str(s).endswith("px") else None


def parse_pct(s):
    return float(str(s).replace("%", "")) if s and str(s).endswith("%") else None


# ── serve_podcast 生命週期 ────────────────────────────────────────────────
def port_busy(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
        return True
    except urllib.error.URLError:
        return False
    except Exception:
        return True


def start_server():
    log = open(SANDBOX_ROOT / "server-capstyle.log", "ab")
    p = subprocess.Popen(
        [sys.executable, str(HERE / "serve_podcast.py")], stdout=log, stderr=log
    )
    for _ in range(120):
        if p.poll() is not None:
            raise RuntimeError("serve_podcast 啟動即退出，看 server-capstyle.log")
        if port_busy(POD_PORT):
            time.sleep(0.6)  # uvicorn 剛 bind，等路由掛完
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
    for _ in range(40):  # 等 port 真的釋放，否則下一個變體 bind 失敗
        if not port_busy(POD_PORT):
            return
        time.sleep(0.25)


def yaml_with_style(base_text: str, style: dict) -> str:
    lines = [base_text.rstrip("\n"), "subtitle_style:"]
    for k, v in style.items():
        lines.append(f'  {k}: {json.dumps(v, ensure_ascii=False)}')
    return "\n".join(lines) + "\n"


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
    """開 podcast 編輯器，等狀態載入（overlay 已被套上 inline 字級）。"""
    page = await C.open_page(browser, ws, sessions, POD, settle=2.0)
    ready = await wait_for(
        page,
        "(() => { const o = document.querySelector('#caption-overlay');"
        " return !!(o && o.style.fontSize); })()",
    )
    return page, ready


async def video_ready(page):
    return await C.js(
        page,
        "(() => { const v=document.querySelector('#video');"
        " return v ? {readyState:v.readyState, seekableLen:v.seekable.length,"
        " dur:v.duration} : null; })()",
    )


async def seek_to_caption(page):
    """seek 到第一句字幕的中點，讓 overlay 真的長出 .caption-line。"""
    t = await C.js(
        page,
        "(() => { const b = document.querySelector('#card-timeline .tl-block');"
        " if (!b) return null; const s=Number(b.dataset.start), e=Number(b.dataset.end);"
        " return (s+e)/2; })()",
    )
    if t is None:
        return None
    await C.js(page, f"(() => {{ document.querySelector('#video').currentTime = {t}; }})()")
    await wait_for(page, "document.querySelectorAll('#caption-overlay .caption-line').length > 0")
    return t


async def measure(page):
    return await C.js(
        page,
        """(() => {
          const o = document.querySelector('#caption-overlay');
          const wrap = document.querySelector('.video-wrap');
          const line = o.querySelector('.caption-line');
          const cs = getComputedStyle(o);
          const ls = line ? getComputedStyle(line) : null;
          return {
            wrapH: wrap ? wrap.clientHeight : null,
            lineCount: o.querySelectorAll('.caption-line').length,
            inline: { fontSize:o.style.fontSize, fontWeight:o.style.fontWeight,
                      color:o.style.color, textShadow:o.style.textShadow,
                      top:o.style.top, bottom:o.style.bottom, transform:o.style.transform },
            computed: { fontSize:cs.fontSize, fontWeight:cs.fontWeight,
                        color:cs.color, textShadow:cs.textShadow },
            line: ls ? { color:ls.color, background:ls.backgroundColor,
                         padding:ls.padding, borderRadius:ls.borderRadius } : null,
          };
        })()""",
    )


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


# ── 突變點（產品碼字串，必須逐字命中）──────────────────────────────────────
MUT1_OLD = "  _captionCss = scale == null || !style ? null : buildSubtitleCss(style, scale);"
MUT1_NEW = (
    "  _captionCss = scale == null || !style ? null"
    " : { fontSize: buildSubtitleCss(style, scale).fontSize };"
)
MUT2_OLD = "  const capBucket = subtitleAlignmentBucket(capStyle?.alignment);"
MUT2_NEW = '  const capBucket = state.activeVersion === "reels" ? "middle" : "bottom";'


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
        logp(f"podcast 字幕樣式走查（W2）  URL={POD}  CDP={CDP_PORT}")
        logp("=" * 70)

        # ══════════ 變體 A：底部 · 描邊 ══════════
        EP_YAML.write_text(yaml_with_style(base_yaml, VARIANTS["A"]["style"]), encoding="utf-8")
        server = start_server()
        page, ready = await open_pod(browser, ws, sessions)
        logp(f"\n--- P1 變體 A：{VARIANTS['A']['desc']} ---")
        C.check("P1.0 podcast 編輯器已載入且 overlay 已套上 inline 樣式", bool(ready), ready, True)

        # 版本指紋：W2 前的 app.js 沒有 applyCaptionStyle／placeCaptionOverlay
        src = APP_JS.read_text(encoding="utf-8")
        fp = {
            "applyCaptionStyle": "function applyCaptionStyle()" in src,
            "placeCaptionOverlay": "function placeCaptionOverlay(" in src,
            "buildSubtitleCss": "buildSubtitleCss" in src,
        }
        C.check("P1.1 版本指紋：app.js 是 W2 後的版本（三個新符號都在）",
                fp == {"applyCaptionStyle": True, "placeCaptionOverlay": True,
                       "buildSubtitleCss": True},
                fp, {"applyCaptionStyle": True, "placeCaptionOverlay": True,
                     "buildSubtitleCss": True})

        vr = await video_ready(page)
        prereq = bool(vr and vr["readyState"] >= 1 and vr["seekableLen"] > 0)
        C.check("P1.2 seek 前提：#video readyState>=1 且 seekable.length>0", prereq, vr,
                "readyState>=1 且 seekableLen>0")
        if not prereq:
            C.skip("P1.* 後續字幕行斷言", "seek 前提不成立（環境不可 seek），不假裝通過")
            C.summary()
            return 1

        t = await seek_to_caption(page)
        m = await measure(page)
        C.check("P1.3 seek 到第一句後 overlay 長出 .caption-line（有字可量）",
                bool(m and m["lineCount"] >= 1), {"t": t, "lineCount": m and m["lineCount"]}, ">=1")

        stA = VARIANTS["A"]["style"]
        wrapH = m["wrapH"]
        want_px = expect_font_px(stA, wrapH)
        got_px = parse_px(m["inline"]["fontSize"])
        C.check(
            f"P1.4 字級＝font_size×(預覽高/輸出高)：{stA['font_size']}×({wrapH}/{OUT_H})≈{want_px:.1f}px",
            got_px is not None and abs(got_px - want_px) < 0.08, got_px, round(want_px, 2))

        want_color = ass_to_rgb(stA["primary_colour"])
        C.check("P1.5 文字顏色吃到 primary_colour（&H0000FFFF → 黃）",
                m["computed"]["color"] == want_color, m["computed"]["color"], want_color)
        C.check("P1.6 字幕行本身的顏色同樣是 primary_colour（app.css 的 speaker 白色沒壓過去）",
                m["line"]["color"] == want_color, m["line"]["color"], want_color)

        want_outline = ass_to_rgb(stA["outline_colour"])
        shadow = m["computed"]["textShadow"] or ""
        C.check("P1.7 描邊顏色吃到 outline_colour，且是 12 向描邊（shadow=0 → 無額外投影）",
                shadow.count(want_outline) == 12 and "rgba(0, 0, 0, 0.9)" not in shadow,
                {"outlineHits": shadow.count(want_outline),
                 "hasDropShadow": "rgba(0, 0, 0, 0.9)" in shadow},
                {"outlineHits": 12, "hasDropShadow": False})
        C.check("P1.8 bold=0 → fontWeight 400（預設 1，證明真的讀 yaml）",
                m["computed"]["fontWeight"] == "400", m["computed"]["fontWeight"], "400")
        C.check("P1.9 border_style=1 → 行無底色塊（背景透明）",
                m["line"]["background"] == "rgba(0, 0, 0, 0)", m["line"]["background"],
                "rgba(0, 0, 0, 0)")

        want_bottom = stA["margin_v"] / OUT_H * 100
        got_bottom = parse_pct(m["inline"]["bottom"])
        C.check(
            f"P1.10 底距＝margin_v/輸出高＝{stA['margin_v']}/{OUT_H}≈{want_bottom:.2f}%（以前寫死 8%）",
            got_bottom is not None and abs(got_bottom - want_bottom) < 0.02,
            got_bottom, round(want_bottom, 2))
        C.check("P1.11 底部對齊時不設 top／transform（落點只由 bottom 決定）",
                (m["inline"]["top"], m["inline"]["transform"]) == ("", ""),
                {"top": m["inline"]["top"], "transform": m["inline"]["transform"]},
                {"top": "", "transform": ""})

        # 互動：字級 +／− 按鈕（真事件點擊 + elementFromPoint 疊層驗證）
        # 字級控制放在「出片設定」<details> 裡，預設收合；收合時 Chrome 會把內容
        # 標成不可命中（elementFromPoint 只會回到外層 .video-pane），所以先真的點開面板。
        summ = await C.element_rect_center(page, ".framing-panel .framing-summary")
        await C.mouse_click(page, summ["cx"], summ["cy"])
        await asyncio.sleep(0.35)
        opened = await C.js(page, "!!document.querySelector('.framing-panel')?.open")
        C.check("P1.12a 點「出片設定」摘要列 → 面板展開（字級控制才摸得到）",
                opened is True, opened, True)
        inc = await C.element_rect_center(page, "#cap-size-inc")
        top_el = await C.top_element_at(page, inc["cx"], inc["cy"])
        C.check("P1.12b 字級「＋」按鈕未被疊層遮擋（elementFromPoint 最上層是它）",
                top_el is not None and top_el.get("id") == "cap-size-inc", top_el,
                {"id": "cap-size-inc"})
        before_px = parse_px(m["inline"]["fontSize"])
        await C.mouse_click(page, inc["cx"], inc["cy"])
        await asyncio.sleep(0.4)
        m2 = await measure(page)
        val_txt = await C.js(page, "document.querySelector('#cap-size-val').textContent")
        after_px = parse_px(m2["inline"]["fontSize"])
        want_after = expect_font_px({"font_size": stA["font_size"] + 2}, wrapH)
        C.check("P1.13 點「＋」→ 數值標籤 +2（74），且預覽字級同步放大（非死碼）",
                val_txt == str(stA["font_size"] + 2) and after_px is not None
                and abs(after_px - want_after) < 0.08 and after_px > before_px,
                {"label": val_txt, "px": after_px}, {"label": "74", "px": round(want_after, 2)})

        errs = C.console_errors(page)
        C.check("P1.14 整段互動無 console 例外", errs == [], errs, [])

        # ── MUT1 真突變：把換算改回「只有字級」（W2 前行為）────────────────
        logp("\n--- MUT1 真突變：applyCaptionStyle 只留字級（產品碼一行）---")
        mut1 = {}
        patch_file(APP_JS, MUT1_OLD, MUT1_NEW)
        try:
            page_r, _ = await open_pod(browser, ws, sessions)
            await seek_to_caption(page_r)
            mr = await measure(page_r)
            red1_ok, _ = raw_eq("MUT1-RED 文字顏色應為 primary_colour（突變後預期退回 app.css 白）",
                                mr["computed"]["color"], want_color)
            mut1["red"] = {"color": mr["computed"]["color"],
                           "fontWeight": mr["computed"]["fontWeight"]}
        finally:
            patch_file(APP_JS, MUT1_NEW, MUT1_OLD)
        page_g, _ = await open_pod(browser, ws, sessions)
        await seek_to_caption(page_g)
        mg = await measure(page_g)
        green1_ok, _ = raw_eq("MUT1-GREEN 還原後文字顏色恢復 primary_colour",
                              mg["computed"]["color"], want_color)
        mut1["green"] = {"color": mg["computed"]["color"],
                         "fontWeight": mg["computed"]["fontWeight"]}
        C.check("MUT1 真突變成立：改回「只設字級」→ 顏色錯成白（RED），還原→恢復黃（GREEN）",
                (red1_ok is False) and (green1_ok is True), mut1,
                {"red_color": "rgb(255, 255, 255)（退回 app.css）", "green_color": want_color})

        stop_server(server)
        server = None

        # ══════════ 變體 B：頂部 · 底色塊 ══════════
        EP_YAML.write_text(yaml_with_style(base_yaml, VARIANTS["B"]["style"]), encoding="utf-8")
        server = start_server()
        pageB, readyB = await open_pod(browser, ws, sessions)
        logp(f"\n--- P2 變體 B：{VARIANTS['B']['desc']} ---")
        C.check("P2.0 變體 B 載入完成", bool(readyB), readyB, True)
        await seek_to_caption(pageB)
        mb = await measure(pageB)
        stB = VARIANTS["B"]["style"]

        want_top = stB["margin_v"] / OUT_H * 100
        got_top = parse_pct(mb["inline"]["top"])
        C.check(f"P2.1 alignment=6 → 字幕置頂，top＝margin_v/輸出高≈{want_top:.2f}%",
                got_top is not None and abs(got_top - want_top) < 0.02, got_top,
                round(want_top, 2))
        C.check("P2.2 置頂時不設 bottom／transform",
                (mb["inline"]["bottom"], mb["inline"]["transform"]) == ("", ""),
                {"bottom": mb["inline"]["bottom"], "transform": mb["inline"]["transform"]},
                {"bottom": "", "transform": ""})

        want_bg = ass_to_rgb(stB["outline_colour"])
        C.check("P2.3 border_style=3 → 行帶不透明底色塊（底色＝outline_colour 藍）",
                mb["line"]["background"] == want_bg, mb["line"]["background"], want_bg)
        C.check("P2.4 底色塊模式取消描邊（textShadow: none）",
                mb["computed"]["textShadow"] == "none", mb["computed"]["textShadow"], "none")
        pad = mb["line"]["padding"]
        C.check("P2.5 底色塊貼著字：行有內距（非 0），而不是鋪滿整條 overlay",
                bool(pad) and pad != "0px" and not pad.startswith("0px 0px"), pad, "非 0 內距")
        C.check("P2.6 bold=1 → fontWeight 700",
                mb["computed"]["fontWeight"] == "700", mb["computed"]["fontWeight"], "700")

        # ── MUT2 真突變：落點改回「Reels 置中／YT 置底」寫死版 ──────────────
        logp("\n--- MUT2 真突變：落點改回寫死 bucket（產品碼一行）---")
        mut2 = {}
        patch_file(APP_JS, MUT2_OLD, MUT2_NEW)
        try:
            pageR, _ = await open_pod(browser, ws, sessions)
            mr2 = await measure(pageR)
            red2_ok, _ = raw_eq("MUT2-RED alignment=6 應置頂（top 有值、bottom 空）",
                                {"top": mr2["inline"]["top"] != "", "bottom": mr2["inline"]["bottom"]},
                                {"top": True, "bottom": ""})
            mut2["red"] = {"top": mr2["inline"]["top"], "bottom": mr2["inline"]["bottom"]}
        finally:
            patch_file(APP_JS, MUT2_NEW, MUT2_OLD)
        pageG, _ = await open_pod(browser, ws, sessions)
        mg2 = await measure(pageG)
        green2_ok, _ = raw_eq("MUT2-GREEN 還原後 alignment=6 恢復置頂",
                              {"top": mg2["inline"]["top"] != "", "bottom": mg2["inline"]["bottom"]},
                              {"top": True, "bottom": ""})
        mut2["green"] = {"top": mg2["inline"]["top"], "bottom": mg2["inline"]["bottom"]}
        C.check("MUT2 真突變成立：落點改回寫死 → 置頂失效落回底部（RED），還原→恢復置頂（GREEN）",
                (red2_ok is False) and (green2_ok is True), mut2,
                {"red": "bottom 有值、top 空", "green": "top≈5.56%、bottom 空"})

        stop_server(server)
        server = None

        # ══════════ 變體 C：正中央 ══════════
        EP_YAML.write_text(yaml_with_style(base_yaml, VARIANTS["C"]["style"]), encoding="utf-8")
        server = start_server()
        pageC, readyC = await open_pod(browser, ws, sessions)
        logp(f"\n--- P3 變體 C：{VARIANTS['C']['desc']} ---")
        C.check("P3.0 變體 C 載入完成", bool(readyC), readyC, True)
        await seek_to_caption(pageC)
        mc = await measure(pageC)
        stC = VARIANTS["C"]["style"]

        want_mid = (0.5 + stC["margin_v"] / OUT_H) * 100
        got_mid = parse_pct(mc["inline"]["top"])
        C.check(f"P3.1 alignment=10 → 正中央＋margin_v 下移，top≈{want_mid:.2f}%",
                got_mid is not None and abs(got_mid - want_mid) < 0.02, got_mid,
                round(want_mid, 2))
        C.check("P3.2 正中央用 translateY(-50%) 把字幕本身置中（非只設 top）",
                mc["inline"]["transform"] == "translateY(-50%)", mc["inline"]["transform"],
                "translateY(-50%)")
        C.check("P3.3 顏色吃到 primary_colour（&H0000FF00 → 綠）",
                mc["computed"]["color"] == ass_to_rgb(stC["primary_colour"]),
                mc["computed"]["color"], ass_to_rgb(stC["primary_colour"]))
        sc = mc["computed"]["textShadow"] or ""
        C.check("P3.4 shadow=1 → 描邊之外另有右下投影（rgba(0, 0, 0, 0.9)）",
                "rgba(0, 0, 0, 0.9)" in sc, "rgba(0, 0, 0, 0.9)" in sc, True)

        stop_server(server)
        server = None

        bad = C.summary()
        for line in C.results_as_dict():
            log_lines.append(f"{line['status'].upper()}\t{line['name']}")
        RESULT_JSON.write_text(
            json.dumps({"mode": "podcast-caption-style-w2", "results": C.results_as_dict()},
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
