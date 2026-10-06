#!/usr/bin/env python3
"""podcast 模式字幕時間軸真事件走查（T0 baseline）。

服務對象：static/timeline.js 的 #card-timeline，經 index.html + app.js（ES module）
載入。縮放倍率的量測一律用 DOM（不依賴 hook）：#card-timeline 寬度、參考 .tl-block
相對 #card-timeline 原點的像素位移、#tl-zoom-fit 按鈕文字（"適合" / "1.6×"，
timeline.js 直接把倍率印在上面）、#tl-zoom-out/#tl-zoom-in 的 disabled 態。
標題卡軌（E 段）另用 app.js 的 window.__pt* 測試 hook 在記憶體造卡——這些 hook 已
閘門化（#6），只在 URL 帶 ?pthook=1 時才掛載；本走查以 ?pthook=1 載入，並在 H 段
反證「不帶 flag 時 hook 全數 undefined」。

前提：先跑 build_sandbox_episode.py 建沙盒集、serve_podcast.py 起在 :8795、
headless Chrome 開 CDP 在 :9331（全新 profile，見 README 慣例）。

跑法：/usr/bin/python3 -u drive_podcast.py
輸出：PASS/FAIL 逐行 + 最後寫 podcast_result.json（本目錄）供 assemble_baseline.py 讀。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp_common as C

BASE = "http://127.0.0.1:8795"
# 標題卡走查用的 hook 已閘門化（#6）：app.js 只在 URL 帶 ?pthook=1 時才掛 window.__pt*，
# 正常發佈/使用的頁面不掛。本走查一律以 ?pthook=1 載入；另在 H 段開一個「不帶 flag」的頁
# 面反證 hook 全數 undefined，證明閘門有效。
FLAG = "?pthook=1"
TL_ZOOM_STEP = 1.6
OUT = Path(__file__).resolve().parent / "podcast_result.json"


async def get_cdp_ws_url():
    import urllib.request

    with urllib.request.urlopen("http://127.0.0.1:9331/json/version", timeout=5) as r:
        return json.loads(r.read())["webSocketDebuggerUrl"]


async def wait_for(page, expr, timeout=40, interval=0.5, desc=""):
    n = int(timeout / interval)
    for _ in range(n):
        v = await C.js(page, expr)
        if v:
            return v
        await asyncio.sleep(interval)
    return None


async def main():
    ws_url = await get_cdp_ws_url()
    browser, sessions, ws = await C.connect(ws_url)
    page = await C.open_page(browser, ws, sessions, BASE + "/" + FLAG, settle=2.0)

    # --- 0. 版本指紋 + 等卡片時間軸出現（README 教訓：來源沒同步會測到舊版/空頁）---
    wrap_hidden = await wait_for(
        page,
        "document.querySelector('#card-timeline-wrap') && "
        "!document.querySelector('#card-timeline-wrap').hidden && "
        "document.querySelectorAll('#card-timeline .tl-block').length > 0",
        timeout=20,
        desc="cards ready",
    )
    C.check("A0 卡片時間軸已渲染（#card-timeline-wrap 解除隱藏、有 .tl-block）", bool(wrap_hidden), wrap_hidden)
    errs = C.console_errors(page)
    C.check("A0b 頁面載入無 console 例外", len(errs) == 0, errs)

    n_blocks = await C.js(page, "document.querySelectorAll('#card-timeline .tl-block').length")
    C.check("A0c .tl-block 數量 > 0", (n_blocks or 0) > 0, n_blocks)

    # ============ 1. 縮放：zoom in 一級 → zoom out 一級 ============
    tl_before = await C.element_rect_center(page, "#card-timeline")
    # 參考塊：挑索引 2（避開第一塊常見的邊界特例）
    ref_idx = min(2, (n_blocks or 1) - 1)
    ref_before = await C.js(
        page,
        f"""(() => {{
          const tl = document.querySelector('#card-timeline');
          const b = document.querySelectorAll('#card-timeline .tl-block')[{ref_idx}];
          if (!tl || !b) return null;
          const tr = tl.getBoundingClientRect(), br = b.getBoundingClientRect();
          return {{ offsetPx: br.left - tr.left, datasetStart: b.dataset.start }};
        }})()""",
    )
    fit_before = await C.js(page, "document.querySelector('#tl-zoom-fit')?.textContent?.trim()")
    out_disabled_before = await C.js(page, "document.querySelector('#tl-zoom-out')?.disabled")
    C.check("B0 初始 #tl-zoom-fit 文字為「適合」（zoom=1）", fit_before == "適合", fit_before, "適合")
    C.check("B0b 初始 #tl-zoom-out 已停用（已在 zoom 下限）", out_disabled_before is True, out_disabled_before)

    zin = await C.element_rect_center(page, "#tl-zoom-in")
    top = await C.top_element_at(page, zin["cx"], zin["cy"])
    C.check(
        "B1 #tl-zoom-in 中心點 elementFromPoint 命中自己（未被疊層吃掉）",
        top is not None and top.get("id") == "tl-zoom-in",
        top,
    )
    await C.mouse_click(page, zin["cx"], zin["cy"])
    await asyncio.sleep(0.4)

    tl_after_in = await C.element_rect_center(page, "#card-timeline")
    ref_after_in = await C.js(
        page,
        f"""(() => {{
          const tl = document.querySelector('#card-timeline');
          const b = document.querySelectorAll('#card-timeline .tl-block')[{ref_idx}];
          const tr = tl.getBoundingClientRect(), br = b.getBoundingClientRect();
          return {{ offsetPx: br.left - tr.left }};
        }})()""",
    )
    fit_after_in = await C.js(page, "document.querySelector('#tl-zoom-fit')?.textContent?.trim()")

    width_ratio_in = tl_after_in["w"] / tl_before["w"] if tl_before["w"] else 0
    C.check(
        f"B2 zoom-in 一級後 #card-timeline 寬度比 ≈{TL_ZOOM_STEP}（{tl_before['w']:.1f}px→{tl_after_in['w']:.1f}px）",
        abs(width_ratio_in - TL_ZOOM_STEP) < 0.05,
        round(width_ratio_in, 4),
        TL_ZOOM_STEP,
    )
    if ref_before and ref_before["offsetPx"] > 0.5:
        block_ratio_in = ref_after_in["offsetPx"] / ref_before["offsetPx"]
        C.check(
            f"B3 參考塊(idx={ref_idx})像素位移比 ≈{TL_ZOOM_STEP}（{ref_before['offsetPx']:.1f}px→{ref_after_in['offsetPx']:.1f}px）",
            abs(block_ratio_in - TL_ZOOM_STEP) < 0.1,
            round(block_ratio_in, 4),
            TL_ZOOM_STEP,
        )
    else:
        # #4：前提不成立時明確 SKIP（不計入 PASS），不再用 check(..., True) 灌水成通過。
        C.skip("B3 參考塊像素位移比", f"參考塊起點太接近 0（offsetPx={ref_before}），無法算有意義的位移比")
    C.check(
        f"B4 zoom-in 後 #tl-zoom-fit 文字顯示倍率「{TL_ZOOM_STEP}×」",
        fit_after_in == f"{TL_ZOOM_STEP:.1f}×",
        fit_after_in,
        f"{TL_ZOOM_STEP:.1f}×",
    )

    zout = await C.element_rect_center(page, "#tl-zoom-out")
    top2 = await C.top_element_at(page, zout["cx"], zout["cy"])
    C.check(
        "B5 #tl-zoom-out 中心點 elementFromPoint 命中自己",
        top2 is not None and top2.get("id") == "tl-zoom-out",
        top2,
    )
    await C.mouse_click(page, zout["cx"], zout["cy"])
    await asyncio.sleep(0.4)
    tl_after_out = await C.element_rect_center(page, "#card-timeline")
    fit_after_out = await C.js(page, "document.querySelector('#tl-zoom-fit')?.textContent?.trim()")
    width_ratio_out = tl_after_out["w"] / tl_after_in["w"] if tl_after_in["w"] else 0
    C.check(
        f"B6 zoom-out 一級後寬度比 ≈{1/TL_ZOOM_STEP:.4f}（回到約原寬 {tl_after_out['w']:.1f}px vs 原 {tl_before['w']:.1f}px）",
        abs(tl_after_out["w"] - tl_before["w"]) < 2.0,
        round(tl_after_out["w"], 2),
        round(tl_before["w"], 2),
    )
    C.check("B7 zoom-out 後 #tl-zoom-fit 文字回到「適合」", fit_after_out == "適合", fit_after_out, "適合")

    # 活性檢查（非固定值；#3 更名——這不是「突變測試」，只改輸入不動產品碼）：連按多次
    # zoom-in，寬度要持續變大，證明 B2 讀到的寬度是 state.tlZoom 驅動的真實值、非某個 CSS
    # 常數。真正的突變測試（把受測產品碼那行改回舊行為→確認變紅）記在 MUTATIONS.md，
    # 對應 B2/Z2 兩組斷言（revert timeline.js:_applyTlZoomWidth 施加對象），非本行職責。
    for _ in range(4):
        b = await C.element_rect_center(page, "#tl-zoom-in")
        await C.mouse_click(page, b["cx"], b["cy"])
        await asyncio.sleep(0.15)
    tl_mut = await C.element_rect_center(page, "#card-timeline")
    C.check(
        "B8 活性檢查：連續多次 zoom-in 後寬度持續增加（非死碼/固定值）",
        tl_mut["w"] > tl_after_in["w"] * 2,
        round(tl_mut["w"], 1),
        f">{tl_after_in['w']*2:.1f}",
    )
    # 收尾：點 fit 讓後面的量測回到 zoom=1 基準（#5：這顆按鈕也是一次真點擊，補疊層驗證）
    fitbtn = await C.element_rect_center(page, "#tl-zoom-fit")
    top_fit = await C.top_element_at(page, fitbtn["cx"], fitbtn["cy"])
    C.check(
        "B8b #tl-zoom-fit 中心點 elementFromPoint 命中自己（未被疊層吃掉）",
        top_fit is not None and top_fit.get("id") == "tl-zoom-fit",
        top_fit,
    )
    await C.mouse_click(page, fitbtn["cx"], fitbtn["cy"])
    await asyncio.sleep(0.4)

    # ============ 2. 播放頭 seek ============
    ready = await C.js(
        page,
        "(() => { const v = document.querySelector('#video'); "
        "return { readyState: v.readyState, seekableLen: v.seekable.length, duration: v.duration }; })()",
    )
    C.check(
        "C0 seek 前提：video.readyState>=1 且 seekable.length>0",
        (ready or {}).get("readyState", 0) >= 1 and (ready or {}).get("seekableLen", 0) > 0,
        ready,
    )

    tl_dataset = await C.js(
        page,
        "(() => { const tl = document.querySelector('#card-timeline'); "
        "return { t0: parseFloat(tl.dataset.t0||'0'), total: parseFloat(tl.dataset.total||'1') }; })()",
    )
    seek_idx = min(3, (n_blocks or 1) - 1)
    blk = await C.js(
        page,
        f"""(() => {{
          const b = document.querySelectorAll('#card-timeline .tl-block')[{seek_idx}];
          if (!b) return null;
          const r = b.getBoundingClientRect();
          return {{ cx: r.left + r.width/2, cy: r.top + r.height/2, start: parseFloat(b.dataset.start) }};
        }})()""",
    )
    top3 = await C.top_element_at(page, blk["cx"], blk["cy"])
    C.check(
        "C1 目標 .tl-block 中心點 elementFromPoint 命中該 block（未被把手/波形蓋掉）",
        top3 is not None and "tl-block" in (top3.get("cls") or ""),
        top3,
    )
    await C.mouse_click(page, blk["cx"], blk["cy"])
    await asyncio.sleep(0.5)
    cur = await C.js(page, "document.querySelector('#video').currentTime")
    C.check(
        f"C2 點擊 .tl-block(idx={seek_idx}) 後 video.currentTime 對到 block.dataset.start",
        abs((cur or -999) - blk["start"]) < 0.05,
        cur,
        blk["start"],
    )
    ph_left = await C.js(page, "document.querySelector('#tl-playhead')?.style?.left")
    expect_pct = max(0, min(100, (blk["start"] - tl_dataset["t0"]) / tl_dataset["total"] * 100))
    got_pct = float((ph_left or "0%").rstrip("%") or 0)
    C.check(
        f"C3 #tl-playhead style.left 對到 seek 後時間換算的百分比",
        abs(got_pct - expect_pct) < 0.5,
        round(got_pct, 3),
        round(expect_pct, 3),
    )

    # 突變測試：seek 到另一個不同時間點，播放頭要跟著移動（不是釘死的固定值）
    seek_idx2 = 0 if seek_idx != 0 else min(1, (n_blocks or 1) - 1)
    blk2 = await C.js(
        page,
        f"""(() => {{
          const b = document.querySelectorAll('#card-timeline .tl-block')[{seek_idx2}];
          const r = b.getBoundingClientRect();
          return {{ cx: r.left + r.width/2, cy: r.top + r.height/2, start: parseFloat(b.dataset.start) }};
        }})()""",
    )
    # #5：blk2 也是一次真點擊，補疊層驗證（原本只驗了 blk 首擊 C1）。
    top_blk2 = await C.top_element_at(page, blk2["cx"], blk2["cy"])
    C.check(
        "C3b 第二個 .tl-block 中心點 elementFromPoint 命中該 block（未被波形/播放頭蓋掉）",
        top_blk2 is not None and "tl-block" in (top_blk2.get("cls") or ""),
        top_blk2,
    )
    await C.mouse_click(page, blk2["cx"], blk2["cy"])
    await asyncio.sleep(0.5)
    ph_left2 = await C.js(page, "document.querySelector('#tl-playhead')?.style?.left")
    C.check(
        "C4 活性檢查：seek 到不同 block 後播放頭 left 值也跟著變（非固定值）",
        ph_left2 != ph_left,
        ph_left2,
        f"≠{ph_left}",
    )

    # ============ 3. 波形：canvas 有實際畫 ============
    async def waveform_painted():
        return await C.js(
            page,
            """(() => {
              const c = document.querySelector('#tl-waveform');
              if (!c || !c.width || !c.height) return { painted: false, reason: 'no-canvas-or-zero-size' };
              const ctx = c.getContext('2d');
              const data = ctx.getImageData(0, 0, c.width, c.height).data;
              let nonZeroAlpha = 0;
              for (let i = 3; i < data.length; i += 4) if (data[i] !== 0) nonZeroAlpha++;
              return { painted: nonZeroAlpha > 0, nonZeroAlpha, w: c.width, h: c.height };
            })()""",
        )

    wf = None
    for _ in range(60):
        wf = await waveform_painted()
        if wf and wf.get("painted"):
            break
        await asyncio.sleep(1.0)
    C.check(
        "D1 #tl-waveform canvas 有實際畫（非零 alpha 像素數 > 0）",
        bool(wf and wf.get("painted")),
        wf,
    )

    # ============ 4. 標題卡軌渲染/事件（T4，docs/plans/2026-09-23-unified-timeline-core.md） ============
    # #1 修正後的真行為：podcast 標題卡軌「預設關」（index.html #tl-card-track 寫死 hidden；
    # timeline.js renderCardTimeline() 不再無條件 setCardTrackVisible(true)），對齊 D1
    # 「podcast 預設關、影片預設開」。走查要先用 __ptSetCardTrackVisible(true) 開軌（取代
    # 過去靠 render 強制開），才進行造卡/量測。
    # #6：這些 window.__pt* hook 已閘門化，只在 ?pthook=1 掛載——本頁帶了 flag，故應存在；
    # H 段另證「不帶 flag 一律 undefined」。
    hooks = await C.js(
        page,
        "(() => ({"
        " setVis: typeof window.__ptSetCardTrackVisible,"
        " cards: typeof window.__ptCards,"
        " height: typeof window.__ptCardTrackHeight,"
        " add: typeof window.__ptAddCard,"
        " select: typeof window.__ptSelectCard,"
        " set: typeof window.__ptSetCard }))()",
    )
    C.check(
        "E-hooks #6：帶 ?pthook=1 時 6 個 __pt* hook 皆為 function（閘門開）",
        bool(hooks) and all(v == "function" for v in hooks.values()),
        hooks,
    )

    # #1：開軌前的預設態必須是「關」——hidden=true、display:none、不佔任何高度（offsetHeight=0）。
    track_default = await C.js(
        page,
        "(() => { const t = document.querySelector('#tl-card-track'); "
        "if (!t) return null; const cs = getComputedStyle(t); "
        "return { hidden: t.hidden, offsetHeight: t.offsetHeight, display: cs.display }; })()",
    )
    C.check(
        "E0 #1 預設態：#tl-card-track 預設「關」（hidden=true、display:none、不佔高度）",
        bool(track_default)
        and track_default["hidden"] is True
        and track_default["offsetHeight"] == 0
        and track_default["display"] == "none",
        track_default,
    )

    # 用測試 hook 開軌（取代 render 強制開）；回傳 true 代表現在可見。
    opened = await C.js(page, "window.__ptSetCardTrackVisible(true)")
    await asyncio.sleep(0.1)
    track_open = await C.js(
        page,
        "(() => { const t = document.querySelector('#tl-card-track'); "
        "const cs = getComputedStyle(t); "
        "return { hidden: t.hidden, offsetHeight: t.offsetHeight, display: cs.display }; })()",
    )
    C.check(
        "E0-open #1：__ptSetCardTrackVisible(true) 後軌可見（hidden=false、44px、display≠none）",
        opened is True
        and track_open["hidden"] is False
        and track_open["offsetHeight"] == 44
        and track_open["display"] != "none",
        {"opened": opened, "track": track_open},
    )
    cards0 = await C.js(page, "window.__ptCards()")
    C.check("E0b 開軌後仍 0 張標題卡", cards0 == [], cards0, [])

    # 造卡座標：沿用 #card-timeline 已算好的 t0/total（tl_dataset，第 2 節已讀），卡寬取
    # total 的一個比例，動態調整確保像素寬 >= 40px（renderCardTrackCore 的把手門檻是 24px，
    # 留安全餘裕）。此處在 zoom=1 造卡量寬即可；#2 修正後 #tl-card-track 與 #card-timeline
    # 已共用同一個 #tl-tracks 容器縮放，任何 zoom 下兩軌對齊（Z 段專門驗這件事）。
    t0d, total = tl_dataset["t0"], tl_dataset["total"]
    track_w = (await C.element_rect_center(page, "#tl-card-track"))["w"]
    frac = max(0.18, 40.0 / track_w)
    c1_start, c1_end = t0d + total * 0.10, t0d + total * (0.10 + frac)
    c2_start, c2_end = t0d + total * (0.10 + frac / 2), t0d + total * (0.10 + frac / 2 + frac)

    c1 = await C.js(page, f"window.__ptAddCard({c1_start}, {c1_end}, '卡1')")
    await asyncio.sleep(0.1)
    h_one_card = await C.js(page, "window.__ptCardTrackHeight()")
    C.check(
        "E1 加入 1 張不重疊卡後高度仍是 44px（1 車道）",
        h_one_card == 44,
        h_one_card,
        44,
    )

    c1_rect = await C.js(
        page,
        """(() => {
          const el = document.querySelector('#tl-card-track .vt-card');
          if (!el) return null;
          return { left: el.style.left, width: el.style.width };
        })()""",
    )
    exp_left = (c1_start - t0d) / total * 100
    exp_width = (c1_end - c1_start) / total * 100
    got_left = float((c1_rect or {}).get("left", "-1%").rstrip("%") or -1)
    got_width = float((c1_rect or {}).get("width", "-1%").rstrip("%") or -1)
    C.check(
        "E1b 卡1 left% 對到共用公式 (start-t0)/total（t0≠0 時的 timeToPct）",
        abs(got_left - exp_left) < 0.05,
        round(got_left, 3),
        round(exp_left, 3),
    )
    C.check(
        "E1c 卡1 width% 對到共用公式 (end-start)/total（durToPct，不減 t0）",
        abs(got_width - exp_width) < 0.05,
        round(got_width, 3),
        round(exp_width, 3),
    )

    c2 = await C.js(page, f"window.__ptAddCard({c2_start}, {c2_end}, '卡2')")
    await asyncio.sleep(0.1)
    h_two_cards = await C.js(page, "window.__ptCardTrackHeight()")
    C.check(
        "E2 加入第2張重疊卡後高度變 80px（assignCardLanes 分成 2 車道）",
        h_two_cards == 80,
        h_two_cards,
        80,
    )
    cards_after2 = await C.js(page, "window.__ptCards()")
    C.check(
        "E2b __ptCards() 回報 2 張卡",
        isinstance(cards_after2, list) and len(cards_after2) == 2,
        cards_after2,
    )

    # 突變測試：把卡2挪到卡1之後、不再重疊 → 高度應收斂回 44px（證明非寫死常數）
    move_start, move_end = t0d + total * 0.6, t0d + total * (0.6 + frac)
    await C.js(page, f"window.__ptSetCard('{c2['id']}', {{ start: {move_start}, end: {move_end} }})")
    await asyncio.sleep(0.1)
    h_after_move = await C.js(page, "window.__ptCardTrackHeight()")
    C.check(
        "E3 突變測試：移開重疊卡後高度收斂回 44px（非固定值）",
        h_after_move == 44,
        h_after_move,
        44,
    )

    # ============ 5. 標題卡真拖曳（F 段，比照 drive_video_proto.py 的 F0-F2）============
    handle_rect = await C.js(
        page,
        """(() => {
          const els = [...document.querySelectorAll('#tl-card-track .vt-card')];
          const el = els.find(e => e.textContent.includes('卡1'));
          if (!el) return { found: false };
          const h = el.querySelector('.vt-card-h.is-r');
          if (!h) return { found: true, noHandle: true, wpx: el.getBoundingClientRect().width };
          const r = h.getBoundingClientRect();
          return { found: true, cx: r.left + r.width / 2, cy: r.top + r.height / 2 };
        })()""",
    )
    C.check("F0 卡1 DOM 元素存在", bool(handle_rect) and handle_rect.get("found"), handle_rect)
    C.check(
        "F1 卡1 右把手(.vt-card-h.is-r)存在（寬度足夠渲染把手，>=24px 門檻）",
        bool(handle_rect) and not handle_rect.get("noHandle"),
        handle_rect,
    )

    top_h = await C.top_element_at(page, handle_rect["cx"], handle_rect["cy"])
    C.check(
        "F1b 把手中心 elementFromPoint 命中把手本身（未被卡片本體蓋掉）",
        top_h is not None and "vt-card-h" in (top_h.get("cls") or ""),
        top_h,
    )

    before_drag_list = await C.js(page, "window.__ptCards()")
    c1_before = next(c for c in before_drag_list if c["id"] == c1["id"])
    await C.mouse_drag(page, handle_rect["cx"], handle_rect["cy"], handle_rect["cx"] + 60, handle_rect["cy"], steps=8)
    await asyncio.sleep(0.1)
    after_drag_list = await C.js(page, "window.__ptCards()")
    c1_after = next(c for c in after_drag_list if c["id"] == c1["id"])
    C.check(
        "F2 拖右把手後 start 不變（只動出點）",
        abs(c1_after["start"] - c1_before["start"]) < 1e-6,
        c1_after["start"],
        c1_before["start"],
    )
    C.check(
        "F2b 拖右把手後 end 變大（拖右延長出點）",
        c1_after["end"] > c1_before["end"],
        c1_after["end"],
        f">{c1_before['end']}",
    )

    body_rect = await C.js(
        page,
        """(() => {
          const els = [...document.querySelectorAll('#tl-card-track .vt-card')];
          const el = els.find(e => e.textContent.includes('卡1'));
          const r = el.getBoundingClientRect();
          return { cx: r.left + r.width / 2, cy: r.top + r.height / 2 };
        })()""",
    )
    dur_before = c1_after["end"] - c1_after["start"]
    await C.mouse_drag(page, body_rect["cx"], body_rect["cy"], body_rect["cx"] + 40, body_rect["cy"], steps=8)
    await asyncio.sleep(0.1)
    after_move_list = await C.js(page, "window.__ptCards()")
    c1_moved = next(c for c in after_move_list if c["id"] == c1["id"])
    dur_after = c1_moved["end"] - c1_moved["start"]
    C.check(
        "F3 拖卡片本體移動後時長不變",
        abs(dur_after - dur_before) < 1e-6,
        round(dur_after, 4),
        round(dur_before, 4),
    )
    C.check(
        "F3b 拖卡片本體移動後 start 前移（往右拖）",
        c1_moved["start"] > c1_after["start"],
        c1_moved["start"],
        f">{c1_after['start']}",
    )

    # ============ Z. #2 縮放對齊：標題卡軌寬度跟著 zoom 縮放、與字幕軌逐像素對齊 ============
    # #2 修正：縮放施加在共用容器 #tl-tracks（timeline.js:_applyTlZoomWidth）。#card-timeline
    # 與 #tl-card-track 都是它的 width:100% 子元素，故任何 zoom 倍率下兩軌等寬且左緣對齊
    # （容差 ≤1px）。舊版只把 inline width 施加在 #card-timeline，zoom>1 時 #tl-card-track
    # 仍停在原寬 → 兩軌右緣錯開（本節就是抓這個回歸）。此時標題卡軌已開、且有卡（E/F 段）。
    def rects_js():
        return (
            "(() => { const q = s => { const el = document.querySelector(s); "
            "if (!el) return null; const r = el.getBoundingClientRect(); "
            "return { left: r.left, width: r.width, right: r.right }; }; "
            "return { tracks: q('#tl-tracks'), tl: q('#card-timeline'), "
            "card: q('#tl-card-track') }; })()"
        )

    z_at1 = await C.js(page, rects_js())
    C.check(
        "Z0 zoom=1 時 #card-timeline 與 #tl-card-track 左緣＋寬度已對齊（≤1px）",
        bool(z_at1)
        and abs(z_at1["tl"]["left"] - z_at1["card"]["left"]) <= 1.0
        and abs(z_at1["tl"]["width"] - z_at1["card"]["width"]) <= 1.0,
        z_at1,
    )

    # 連按 2 級 zoom-in（1.6^2≈2.56×），放大任何錯位。
    for _ in range(2):
        zb = await C.element_rect_center(page, "#tl-zoom-in")
        await C.mouse_click(page, zb["cx"], zb["cy"])
        await asyncio.sleep(0.3)
    z_zoomed = await C.js(page, rects_js())
    tracks_ratio = (
        z_zoomed["tracks"]["width"] / z_at1["tracks"]["width"]
        if z_at1 and z_at1["tracks"]["width"]
        else 0
    )
    C.check(
        f"Z1 #2：zoom-in 2 級後 #tl-tracks 寬度放大 ≈{TL_ZOOM_STEP**2:.4f}×（縮放施加在共用容器）",
        abs(tracks_ratio - TL_ZOOM_STEP**2) < 0.05,
        round(tracks_ratio, 4),
        round(TL_ZOOM_STEP**2, 4),
    )
    C.check(
        "Z2 #2：zoom>1 下 #card-timeline 與 #tl-card-track 仍等寬且左緣對齊（≤1px，核心回歸點）",
        abs(z_zoomed["tl"]["left"] - z_zoomed["card"]["left"]) <= 1.0
        and abs(z_zoomed["tl"]["width"] - z_zoomed["card"]["width"]) <= 1.0,
        {
            "dLeft": round(z_zoomed["tl"]["left"] - z_zoomed["card"]["left"], 3),
            "dWidth": round(z_zoomed["tl"]["width"] - z_zoomed["card"]["width"], 3),
        },
    )
    C.check(
        "Z3 #2：標題卡軌寬度確實隨 zoom 變大（非固定值；舊 bug 會停在原寬）",
        z_zoomed["card"]["width"] > z_at1["card"]["width"] * 2,
        round(z_zoomed["card"]["width"], 1),
        f">{z_at1['card']['width']*2:.1f}",
    )
    # 收尾回 zoom=1，避免影響後續（若還有）量測。
    fitb = await C.element_rect_center(page, "#tl-zoom-fit")
    await C.mouse_click(page, fitb["cx"], fitb["cy"])
    await asyncio.sleep(0.3)

    # ============ 6. D3 不寫檔驗證：真存檔流程攔截 + episode.yaml 落地檢查 ============
    # 靜態讀碼已確認 api.js 的 buildSavePayload() 沒有 titleCards 欄位（本梯未改動 api.js）；
    # 這裡再用真實存檔流程做動態驗證：攔截 window.fetch，點存檔鈕，檢查送到 /api/save 的
    # body 沒有任何標題卡痕跡，並比對磁碟上的 episode.yaml 內容。
    await C.js(
        page,
        """(() => {
          window.__ptSaveCalls = [];
          const orig = window.fetch;
          window.fetch = function (url, opts) {
            if (typeof url === 'string' && url.includes('/api/save')) {
              window.__ptSaveCalls.push((opts && opts.body) || '');
            }
            return orig.apply(this, arguments);
          };
        })()""",
    )
    save_btn = await C.element_rect_center(page, "#save-btn")
    await C.mouse_click(page, save_btn["cx"], save_btn["cy"])
    await asyncio.sleep(2.0)
    save_bodies = await C.js(page, "window.__ptSaveCalls")
    leak_needles = ["titleCard", "title_card", "卡1", "卡2"]
    has_leak = any(
        any(needle in body for needle in leak_needles) for body in (save_bodies or [])
    )
    C.check(
        "G0 D3：/api/save 送出的 payload 沒有任何標題卡欄位／文字外洩",
        bool(save_bodies) and not has_leak,
        {"nCalls": len(save_bodies or []), "leak": has_leak},
    )

    episode_yaml = Path(
        "/private/tmp/pt-timeline-baseline/episode/20260601 時間軸基準集/episode.yaml"
    )
    yaml_text = episode_yaml.read_text(encoding="utf-8") if episode_yaml.exists() else None
    yaml_has_leak = bool(yaml_text) and any(needle in yaml_text for needle in leak_needles)
    C.check(
        "G1 D3：重新讀回磁碟 episode.yaml 沒有任何標題卡欄位／文字（存檔後未落地）",
        yaml_text is not None and not yaml_has_leak,
        {"exists": yaml_text is not None, "leak": yaml_has_leak},
    )

    # ============ H. #6 閘門反證：不帶 ?pthook=1 的頁面一律不掛 window.__pt* hook ============
    # 另開一個「乾淨」頁（不帶 flag），等卡片時間軸渲染後檢查 6 個 hook 全數 undefined，
    # 證明測試 hook 只在 dev flag 下掛載、正常發佈/使用的 app.js 不會污染 window。
    page2 = await C.open_page(browser, ws, sessions, BASE + "/", settle=2.0)
    await wait_for(
        page2,
        "document.querySelectorAll('#card-timeline .tl-block').length > 0",
        timeout=20,
        desc="cards ready (no-flag)",
    )
    hooks_noflag = await C.js(
        page2,
        "(() => ({"
        " setVis: typeof window.__ptSetCardTrackVisible,"
        " cards: typeof window.__ptCards,"
        " height: typeof window.__ptCardTrackHeight,"
        " add: typeof window.__ptAddCard,"
        " select: typeof window.__ptSelectCard,"
        " set: typeof window.__ptSetCard }))()",
    )
    C.check(
        "H0 #6：不帶 ?pthook=1 時 6 個 __pt* hook 全數 undefined（閘門有效，正常頁面不掛）",
        bool(hooks_noflag) and all(v == "undefined" for v in hooks_noflag.values()),
        hooks_noflag,
    )
    # 反證閘門下標題卡軌仍是預設「關」（#1 在無 hook 的正常頁面同樣成立）。
    track_noflag = await C.js(
        page2,
        "(() => { const t = document.querySelector('#tl-card-track'); "
        "if (!t) return null; const cs = getComputedStyle(t); "
        "return { hidden: t.hidden, offsetHeight: t.offsetHeight, display: cs.display }; })()",
    )
    C.check(
        "H1 #1：正常頁面（無 flag）標題卡軌仍預設「關」（hidden=true、不佔高度）",
        bool(track_noflag)
        and track_noflag["hidden"] is True
        and track_noflag["offsetHeight"] == 0,
        track_noflag,
    )

    OUT.write_text(
        json.dumps({"mode": "podcast", "results": C.results_as_dict()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    bad = C.summary()
    await ws.close()
    return len(bad) == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
