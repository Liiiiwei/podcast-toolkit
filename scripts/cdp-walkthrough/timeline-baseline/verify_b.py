#!/usr/bin/env python3
"""梯次 B（最小版）獨立驗收走查 —— 改動者不驗收，本檔由 fresh-context 驗收者撰寫。

只依「原始需求（docs/plans/2026-09-23-unified-timeline-core.md 驗收總則＋梯次 B 拆解）
＋待驗檔案（timeline.js / timeline-core.js / api.js / app.js / episode_io.py）」判斷，
不信任任何「我已接好」。每個 ✓ 綁布林斷言 ok=實得==期待；拖曳一律走完整序列
（Input.dispatchMouseEvent down→多次 move→up）；seek 前先斷言 seekable.length>0；
可點元素驗 elementFromPoint 最上層真的是它。

六項各自對應一支子走查（以 CLI 參數選跑，預設全跑）：
  b3a  scrub 播放頭下放（podcast）
  b3b  字幕塊整段平移下放（podcast）
  b2   標題卡 podcast 真存檔 round-trip
  b4   layout_mode 控標題卡軌顯隱（video/podcast 兩態）
  b3c  框選 marquee → 併入 deletions（podcast）
  video 影片模式回歸（共用核心 timeline-core.js 未被梯次 B 弄壞）

前提：serve_podcast.py 起在 :8795（服務 live working tree）、range_server.py 以
ROOT=static 起在 :8796（206 Range，video 原型可 seek）、headless Chrome CDP 在 :9331。
跑法：/usr/bin/python3 -u verify_b.py [item ...]
"""
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp_common as C

POD = "http://127.0.0.1:8795/?pthook=1"
VID = "http://127.0.0.1:8796/video-edit-prototype.html?demo=1"
EPDIR = "/private/tmp/pt-timeline-baseline/episode/20260601 時間軸基準集"


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


async def open_pod(browser, ws, sessions, url=POD):
    page = await C.open_page(browser, ws, sessions, url, settle=2.0)
    ready = await wait_for(
        page,
        "document.querySelector('#card-timeline-wrap') && "
        "!document.querySelector('#card-timeline-wrap').hidden && "
        "document.querySelectorAll('#card-timeline .tl-block').length > 0",
    )
    return page, ready


async def blocks_info(page):
    """回傳每個 .tl-block 的 rect + dataset，供選塊與量位移。"""
    return await C.js(
        page,
        """(() => {
          const tl = document.querySelector('#card-timeline');
          const tr = tl.getBoundingClientRect();
          return [...tl.querySelectorAll('.tl-block')].map((b,i) => {
            const r = b.getBoundingClientRect();
            return { i, key: b.dataset.key,
                     dsStart: Number(b.dataset.start), dsEnd: Number(b.dataset.end),
                     left: r.left, right: r.right, width: r.width,
                     cx: r.left + r.width/2, cy: r.top + r.height/2 };
          });
        })()""",
    )


async def video_ready(page):
    return await C.js(
        page,
        "(() => { const v=document.querySelector('#video'); return v?{readyState:v.readyState, seekableLen:v.seekable.length, dur:v.duration}:null; })()",
    )


# ============================================================ B3a scrub 播放頭
async def item_b3a(browser, ws, sessions):
    print("\n---- B3a scrub 播放頭下放（podcast）----", flush=True)
    page, ready = await open_pod(browser, ws, sessions)
    C.check("B3a.0 podcast 時間軸已渲染（有 .tl-block）", bool(ready), ready)

    vr = await video_ready(page)
    prereq = bool(vr and vr["readyState"] >= 1 and vr["seekableLen"] > 0)
    C.check("B3a.1 seek 前提：#video readyState>=1 且 seekable.length>0", prereq, vr)
    if not prereq:
        C.skip("B3a.* 後續刮動斷言", "seek 前提不成立（環境不可 seek），不假裝通過")
        return

    grip = await C.element_rect_center(page, "#tl-playhead-grip")
    top = await C.top_element_at(page, grip["cx"], grip["cy"])
    C.check(
        "B3a.2 #tl-playhead-grip 中心 elementFromPoint 最上層是自己（未被疊層吃掉）",
        top is not None and top.get("id") == "tl-playhead-grip",
        top,
    )

    tl = await C.element_rect_center(page, "#card-timeline")
    geo = await C.js(
        page,
        "(() => { const t=document.querySelector('#card-timeline'); return {t0:Number(t.dataset.t0)||0, total:Number(t.dataset.total)||1}; })()",
    )
    before = await C.js(page, "document.querySelector('#video').currentTime")
    # 拖到時間軸 60% 處（完整序列 down→多次 move→up）
    tx = tl["x"] + tl["w"] * 0.60
    await C.mouse_drag(page, grip["cx"], grip["cy"], tx, grip["cy"], steps=10, settle=0.05)
    await asyncio.sleep(0.3)
    after = await C.js(page, "document.querySelector('#video').currentTime")
    expect = geo["t0"] + 0.60 * geo["total"]
    C.check(
        f"B3a.3 拖曳 grip 到 60% 後 currentTime≈{expect:.2f}s（實得 vs 期待）",
        abs((after or -999) - expect) < max(0.6, geo["total"] * 0.06),
        round(after or -999, 3),
        round(expect, 3),
    )
    C.check(
        "B3a.4 刮動確實改變 currentTime（非靜默失效）",
        abs((after or 0) - (before or 0)) > 0.3,
        {"before": before, "after": after},
    )
    # 第二次拖到 30% → currentTime 要再變（證明每次 move 都 seek，非死值）
    grip2 = await C.element_rect_center(page, "#tl-playhead-grip")
    tx2 = tl["x"] + tl["w"] * 0.30
    await C.mouse_drag(page, grip2["cx"], grip2["cy"], tx2, grip2["cy"], steps=10, settle=0.05)
    await asyncio.sleep(0.3)
    after2 = await C.js(page, "document.querySelector('#video').currentTime")
    C.check(
        "B3a.5 再拖到 30% 後 currentTime 隨 move 再變一次（非固定值）",
        abs((after2 or 0) - (after or 0)) > 0.3 and after2 < after,
        {"t60": after, "t30": after2},
    )


# ============================================================ B3b 整段平移
async def item_b3b(browser, ws, sessions):
    print("\n---- B3b 字幕塊整段平移下放（podcast）----", flush=True)
    page, ready = await open_pod(browser, ws, sessions)
    C.check("B3b.0 podcast 時間軸已渲染", bool(ready), ready)
    vr = await video_ready(page)
    C.check("B3b.1 seek 前提：#video seekable.length>0（純點擊仍跳播放頭需可 seek）",
            bool(vr and vr["readyState"] >= 1 and vr["seekableLen"] > 0), vr)

    bs = await blocks_info(page)
    geo = await C.js(page, "(() => { const t=document.querySelector('#card-timeline'); const r=t.getBoundingClientRect(); return {t0:Number(t.dataset.t0)||0, total:Number(t.dataset.total)||1, w:r.width}; })()")
    px_per_s = geo["w"] / geo["total"]
    # 選一個中段塊（有前後鄰句），且與前一句有間距可平移
    idx = None
    for k in range(1, len(bs) - 1):
        if bs[k]["width"] > 20:
            idx = k
            break
    if idx is None:
        C.skip("B3b.* 平移斷言", "找不到合適的中段字幕塊")
        return
    blk, prv, nxt = bs[idx], bs[idx - 1], bs[idx + 1]

    # 整段平移：拖塊「本體中心」右移 ~30px（避開兩端把手）
    before_w = blk["width"]
    dx = 30
    await C.mouse_drag(page, blk["cx"], blk["cy"], blk["cx"] + dx, blk["cy"], steps=8, settle=0.04)
    await asyncio.sleep(0.3)
    bs2 = await blocks_info(page)
    blk2 = next(b for b in bs2 if b["key"] == blk["key"])
    dLeft = blk2["left"] - blk["left"]
    dW = blk2["width"] - before_w
    C.check(
        "B3b.2 整段平移：塊左緣右移（位移>4px）",
        dLeft > 4,
        round(dLeft, 1),
        ">4",
    )
    C.check(
        "B3b.3 整段平移保長度：寬度幾乎不變（|Δwidth|<2px，證明 start/end 等量平移非改端點）",
        abs(dW) < 2.0,
        round(dW, 2),
        "≈0",
    )

    # 夾制：對同一塊做超大右拖，右緣不得壓過後一句左緣（clamp 到 next.start）
    blk3info = next(b for b in (await blocks_info(page)) if b["key"] == blk["key"])
    await C.mouse_drag(page, blk3info["cx"], blk3info["cy"], blk3info["cx"] + 600, blk3info["cy"], steps=12, settle=0.03)
    await asyncio.sleep(0.3)
    bs4 = await blocks_info(page)
    blk4 = next(b for b in bs4 if b["key"] == blk["key"])
    nxt4 = next(b for b in bs4 if b["key"] == nxt["key"])
    C.check(
        "B3b.4 鄰句夾制：超大右拖後塊右緣不壓過後一句左緣（clamp 到 next.start）",
        blk4["right"] <= nxt4["left"] + 2.0,
        {"blkRight": round(blk4["right"], 1), "nextLeft": round(nxt4["left"], 1)},
    )

    # 純點擊（無位移）仍跳播放頭：用另一個未動過的塊，down→up 同點（不 move）
    fresh = None
    for b in bs4:
        if b["key"] not in (blk["key"],) and b["width"] > 20:
            fresh = b
            break
    if fresh:
        await C.js(page, "document.querySelector('#video').currentTime = 0")
        await asyncio.sleep(0.1)
        await C.mouse_click(page, fresh["cx"], fresh["cy"])
        await asyncio.sleep(0.3)
        ct = await C.js(page, "document.querySelector('#video').currentTime")
        C.check(
            "B3b.5 純點擊（無位移）仍跳播放頭到該塊 start（未被平移誤觸）",
            abs((ct or -999) - fresh["dsStart"]) < 0.25,
            round(ct or -999, 3),
            round(fresh["dsStart"], 3),
        )
    else:
        C.skip("B3b.5 純點擊跳播放頭", "找不到未動過的塊")


# ============================================================ B2 標題卡真存檔
async def item_b2(browser, ws, sessions):
    print("\n---- B2 標題卡 podcast 真存檔 round-trip ----", flush=True)
    page, ready = await open_pod(browser, ws, sessions)
    C.check("B2.0 podcast 時間軸已渲染、__ptAddCard 可用",
            bool(ready) and (await C.js(page, "typeof window.__ptAddCard === 'function'")),
            ready)

    marker = "驗收標題卡A"
    added = await C.js(page, f"window.__ptAddCard(3.0, 6.0, {json.dumps(marker)})")
    cards_mem = await C.js(page, "window.__ptCards()")
    in_mem = any(c.get("text") == marker for c in (cards_mem or []))
    C.check("B2.1 __ptAddCard 後記憶體 state.titleCards 含該卡", in_mem, added)

    # 觸發真存檔：點 #save-btn（完整序列），等存檔鏈完成
    sb = await C.element_rect_center(page, "#save-btn")
    # 存檔鈕內含 <span> 標籤 → elementFromPoint 命中子節點；驗「最上層元素落在 #save-btn 內」即證未被遮罩
    top_in_btn = await C.js(
        page,
        f"(() => {{ const el = document.elementFromPoint({sb['cx']}, {sb['cy']}); return !!(el && el.closest('#save-btn')); }})()",
    )
    C.check("B2.2 #save-btn 中心 elementFromPoint 落在存檔鈕內（未被疊層遮罩，可點）",
            top_in_btn is True, top_in_btn)
    await C.mouse_click(page, sb["cx"], sb["cy"])
    # 等按鈕重新啟用（存檔完成後 handler 會 loadEpisodeState + 解除 disabled）
    await wait_for(page, "document.querySelector('#save-btn') && !document.querySelector('#save-btn').disabled", timeout=15)
    await asyncio.sleep(0.5)

    # round-trip：重開全新頁面（真重載），讀回標題卡
    page2, ready2 = await open_pod(browser, ws, sessions)
    C.check("B2.3 重載頁面時間軸再次渲染", bool(ready2), ready2)
    cards_rt = await C.js(page2, "window.__ptCards()")
    hit = next((c for c in (cards_rt or []) if c.get("text") == marker), None)
    C.check(
        "B2.4 存檔→重載後標題卡仍在（round-trip 生效），且帶 id",
        hit is not None and bool(hit.get("id")),
        hit,
    )


# ============================================================ B4 layout_mode 顯隱
async def set_layout(mode):
    body = json.dumps({"layout_mode": mode, "episode_dir": EPDIR}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8795/api/save", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.status


async def track_state(page):
    return await C.js(
        page,
        """(() => {
          const t = document.querySelector('#tl-card-track');
          if (!t) return null;
          const cs = getComputedStyle(t);
          return { hidden: t.hidden, offsetHeight: t.offsetHeight, display: cs.display };
        })()""",
    )


async def item_b4(browser, ws, sessions):
    print("\n---- B4 layout_mode 控標題卡軌顯隱（兩態）----", flush=True)
    try:
        # video 態
        st = await set_layout("video")
        C.check("B4.0 POST /api/save layout_mode=video 回 200（寫入端）", st == 200, st)
        page_v, ready_v = await open_pod(browser, ws, sessions)
        C.check("B4.1 video 態頁面時間軸渲染", bool(ready_v), ready_v)
        lm_v = await C.js(page_v, "typeof state!=='undefined'")  # state 非全域，預期 undefined
        ts_v = await track_state(page_v)
        C.check(
            "B4.2 layout_mode=video：#tl-card-track 顯示（未 hidden 且 offsetHeight>0）",
            ts_v is not None and ts_v["hidden"] is False and ts_v["offsetHeight"] > 0,
            ts_v,
        )
        # podcast 態
        st2 = await set_layout("podcast")
        C.check("B4.3 POST /api/save layout_mode=podcast 回 200", st2 == 200, st2)
        page_p, ready_p = await open_pod(browser, ws, sessions)
        C.check("B4.4 podcast 態頁面時間軸渲染", bool(ready_p), ready_p)
        ts_p = await track_state(page_p)
        C.check(
            "B4.5 layout_mode=podcast：#tl-card-track 隱藏且不佔位（hidden 且 offsetHeight==0）",
            ts_p is not None and ts_p["hidden"] is True and ts_p["offsetHeight"] == 0,
            ts_p,
        )
    finally:
        # 收尾：確保還原成 podcast，不污染其他子走查
        try:
            await set_layout("podcast")
        except Exception:
            pass


# ============================================================ B3c marquee → deletions
async def item_b3c(browser, ws, sessions):
    print("\n---- B3c 框選 marquee → 併入 deletions（podcast）----", flush=True)
    page, ready = await open_pod(browser, ws, sessions)
    C.check("B3c.0 podcast 時間軸已渲染", bool(ready), ready)
    tl = await C.element_rect_center(page, "#card-timeline")
    deleted0 = await C.js(page, "document.querySelectorAll('#card-timeline .tl-block.deleted').length")

    # 誤點防呆：背景微小位移（<4px）不得選取任何卡
    bg_y = tl["y"] + tl["h"] * 0.5
    x_start = tl["x"] + tl["w"] * 0.30
    await C.mouse_drag(page, x_start, bg_y, x_start + 2, bg_y, steps=3, settle=0.03)
    await asyncio.sleep(0.2)
    deleted_tiny = await C.js(page, "document.querySelectorAll('#card-timeline .tl-block.deleted').length")
    C.check(
        "B3c.1 微小位移（<4px）不選取（deleted 數不變）",
        deleted_tiny == deleted0,
        {"before": deleted0, "afterTiny": deleted_tiny},
    )

    # 起手點必須落在時間軸背景（非塊）：取兩塊之間的空隙 y=背景。用一段橫向大框選涵蓋中段數塊。
    x_lo = tl["x"] + tl["w"] * 0.35
    x_hi = tl["x"] + tl["w"] * 0.75
    # 起點需 target===tl：貼近軌道頂端（塊有高度但背景在塊上下緣仍是 tl）；用軌道垂直中線多半命中塊，
    # 故起手點放在軌道底部 2px 帶（背景），再水平拖。
    start_top = await C.top_element_at(page, x_lo, tl["y"] + tl["h"] - 2)
    y_bg = tl["y"] + tl["h"] - 2
    if not (start_top and start_top.get("id") == "card-timeline"):
        # 後備：頂端 2px
        y_bg = tl["y"] + 2
        start_top = await C.top_element_at(page, x_lo, y_bg)
    C.check(
        "B3c.2 框選起手點落在 #card-timeline 背景（elementFromPoint 命中軌道本身，非塊）",
        start_top is not None and start_top.get("id") == "card-timeline",
        start_top,
    )
    await C.mouse_drag(page, x_lo, y_bg, x_hi, y_bg, steps=12, settle=0.04)
    await asyncio.sleep(0.4)
    deleted1 = await C.js(page, "document.querySelectorAll('#card-timeline .tl-block.deleted').length")
    C.check(
        "B3c.3 框選涵蓋中段多塊後 → 被涵蓋卡進 deletions（.deleted 數增加）",
        deleted1 > deleted0,
        {"before": deleted0, "afterMarquee": deleted1},
    )


# ============================================================ video 回歸（共用核心）
async def open_vid(browser, ws, sessions):
    page = await C.open_page(browser, ws, sessions, VID, settle=2.5)
    ready = await wait_for(
        page,
        "document.querySelectorAll('#vt-sub-track .vt-sub').length>0 && typeof window.__vt==='object' && window.__vt.duration>0",
        timeout=20,
    )
    return page, ready


async def item_video(browser, ws, sessions):
    print("\n---- video 影片模式回歸（共用核心 timeline-core.js）----", flush=True)
    page, ready = await open_vid(browser, ws, sessions)
    C.check("V.0 影片原型 demo 已渲染（.vt-sub 存在、__vt.duration>0）", bool(ready), ready)
    errs = C.console_errors(page)
    C.check("V.0b 載入無 console 例外", len(errs) == 0, errs)

    # 車道分配（共用 assignCardLanes）：2 張重疊卡 → 軌高 80px（2 車道）
    h0 = await C.js(page, "window.__vtCardTrackHeight()")
    C.check("V.1 初始標題卡軌高度 44px（0 張卡）", h0 == 44, h0, 44)
    c1 = await C.js(page, "window.__vtDropTpl('big', 2)")
    c2 = await C.js(page, "window.__vtDropTpl('lower', 3)")  # 2-5 與 3-6 重疊
    h2 = await C.js(page, "window.__vtCardTrackHeight()")
    C.check("V.2 兩張時間重疊卡 → 軌高 80px（assignCardLanes 分 2 車道）", h2 == 80, h2, 80)

    # 端點拖曳（共用 bindCardTimeDragCore 'end' 模式）：先拉寬 c1 讓右把手可見
    if c1:
        await C.js(page, f"window.__vtSelectCard({c1['id']}); window.__vtSetCard({{start:1, end:7}})")
    cards_before = await C.js(page, "window.__vtCards()")
    handle = await C.element_rect_center(page, "#vt-card-track .vt-card .vt-card-h.is-r")
    if handle is None:
        zb = await C.element_rect_center(page, "#vt-zoom-in")
        await C.mouse_click(page, zb["cx"], zb["cy"])
        await asyncio.sleep(0.3)
        handle = await C.element_rect_center(page, "#vt-card-track .vt-card .vt-card-h.is-r")
    C.check("V.3 找到右端點把手 .vt-card-h.is-r", handle is not None, handle)
    if handle:
        top = await C.top_element_at(page, handle["cx"], handle["cy"])
        C.check("V.3b 端點把手中心 elementFromPoint 命中把手（未被卡本體蓋掉）",
                top is not None and "vt-card-h" in (top.get("cls") or ""), top)
        await C.mouse_drag(page, handle["cx"], handle["cy"], handle["cx"] + 60, handle["cy"], steps=8, settle=0.05)
        await asyncio.sleep(0.3)
        cards_after = await C.js(page, "window.__vtCards()")
        b = next((c for c in (cards_before or []) if c["id"] == c1["id"]), None)
        a = next((c for c in (cards_after or []) if c["id"] == c1["id"]), None)
        C.check(
            "V.4 拖右端點把手 → 該卡 end 變長、start 不變（共用 bindCardTimeDragCore 未壞）",
            bool(b and a and a["end"] != b["end"] and a["start"] == b["start"]),
            {"before": b, "after": a},
        )
    else:
        C.check("V.4 拖右端點把手 → end 變（略過：把手未出現）", False, None)


ITEMS = {
    "b3a": item_b3a, "b3b": item_b3b, "b2": item_b2,
    "b4": item_b4, "b3c": item_b3c, "video": item_video,
}


async def main():
    which = [a for a in sys.argv[1:] if a in ITEMS] or list(ITEMS.keys())
    url = await ws_url()
    browser, sessions, ws = await C.connect(url)
    for name in which:
        try:
            await ITEMS[name](browser, ws, sessions)
        except Exception as e:
            C.check(f"{name} 走查未拋例外", False, repr(e))
    bad = C.summary()
    out = Path(__file__).resolve().parent / "verify_b_result.json"
    out.write_text(json.dumps({"ran": which, "results": C.results_as_dict()}, ensure_ascii=False, indent=2), encoding="utf-8")
    await ws.close()
    return len(bad) == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
