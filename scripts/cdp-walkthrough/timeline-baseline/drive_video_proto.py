#!/usr/bin/env python3
"""影片模式原型時間軸真事件走查（T0 baseline）。

服務對象：static/video-edit-prototype.html + video-edit-prototype.js 的
#vt-tracks 三軌堆疊（?demo=1 用內建 sample-video.mp4 + sample-subtitles.json，
不需 server/開集即可整頁渲染，DEMO 判斷見 video-edit-prototype.js:17）。

重要更正（曾在分析階段誤判，這裡記錄避免重蹈）：
縮放實際套用寬度的元素是 #vt-tracks（applyZoomWidth()，
video-edit-prototype.js:1933-1938 = `$("vt-tracks").style.width = zoom*100%`），
不是 #vt-timeline —— #vt-timeline 只是 setZoom()/timeFromEvent() 拿來算錨點與時間
換算用的參考寬度（父層之一，寬度會隨 #vt-tracks 等比變動，仍可佐證但不是本體）。
video 模式沒有 #vt-zoom-fit 這種文字倍率標籤（podcast 模式 timeline.js:294-296 有），
只能量 #vt-tracks 寬度比、參考 .vt-sub 塊像素位移比、#vt-zoom-out/#vt-zoom-in 的
disabled 態。

播放頭刮動（bindPlayheadScrub，video-edit-prototype.js:1975-2000）綁的是
pointerdown/pointermove/pointerup（不是 mouseup/mousedown），但 CDP 的
Input.dispatchMouseEvent 對滑鼠輸入一樣會觸發瀏覽器原生合成的 pointer 事件
（Chrome 的滑鼠輸入管線本就會先產生 PointerEvent 再產生 MouseEvent），
既有 vt-realmode 走查已驗證這條路徑可行，這裡沿用同一手法（真事件
down→多次 move→up 序列，不用 window.__vtSeek 驅動，只用它來讀狀態/佈設）。

前提：video-edit-prototype 的 static 目錄已用 range_server.py 起在 :8796
（支援 206 Range，sample-video.mp4 才能被判定為可 seek）；headless Chrome CDP
在 :9331（沿用 drive_podcast.py 開的同一個瀏覽器 profile/實例，另開分頁測）。

跑法：/usr/bin/python3 -u drive_video_proto.py
輸出：PASS/FAIL 逐行 + 最後寫 video_result.json（本目錄）供 assemble_baseline.py 讀。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp_common as C

BASE = "http://127.0.0.1:8796/video-edit-prototype.html?demo=1"
ZOOM_STEP = 1.6
OUT = Path(__file__).resolve().parent / "video_result.json"


async def get_cdp_ws_url():
    import urllib.request

    with urllib.request.urlopen("http://127.0.0.1:9331/json/version", timeout=5) as r:
        return json.loads(r.read())["webSocketDebuggerUrl"]


async def wait_for(page, expr, timeout=40, interval=0.5):
    n = int(timeout / interval)
    for _ in range(n):
        v = await C.js(page, expr)
        if v:
            return v
        await asyncio.sleep(interval)
    return None


async def ref_sub_offset(page, idx):
    return await C.js(
        page,
        f"""(() => {{
          const track = document.querySelector('#vt-sub-track');
          const b = document.querySelectorAll('#vt-sub-track .vt-sub')[{idx}];
          if (!track || !b) return null;
          const tr = track.getBoundingClientRect(), br = b.getBoundingClientRect();
          return {{ offsetPx: br.left - tr.left }};
        }})()""",
    )


async def main():
    ws_url = await get_cdp_ws_url()
    browser, sessions, ws = await C.connect(ws_url)
    page = await C.open_page(browser, ws, sessions, BASE, settle=2.5)

    # --- 0. 版本指紋 + 等 demo 資料渲染完（README 教訓：來源沒同步會測到舊版/空頁）---
    subs_ready = await wait_for(
        page,
        "document.querySelectorAll('#vt-sub-track .vt-sub').length > 0 && "
        "typeof window.__vt === 'object' && window.__vt.duration > 0",
        timeout=20,
    )
    C.check("A0 demo 字幕軌已渲染（.vt-sub 存在、__vt.duration>0）", bool(subs_ready), subs_ready)
    errs = C.console_errors(page)
    C.check("A0b 頁面載入無 console 例外", len(errs) == 0, errs)

    n_subs = await C.js(page, "document.querySelectorAll('#vt-sub-track .vt-sub').length")
    C.check("A0c .vt-sub 數量 > 0", (n_subs or 0) > 0, n_subs)

    # ============ 1. 縮放：zoom in 一級 → zoom out 一級（#vt-tracks 為本體）============
    tracks_before = await C.element_rect_center(page, "#vt-tracks")
    ref_idx = min(2, (n_subs or 1) - 1)
    ref_before = await ref_sub_offset(page, ref_idx)
    out_disabled_before = await C.js(page, "document.querySelector('#vt-zoom-out')?.disabled")
    in_disabled_before = await C.js(page, "document.querySelector('#vt-zoom-in')?.disabled")
    C.check("B0 初始 #vt-zoom-out 已停用（已在 zoom 下限 ZOOM_MIN=1）", out_disabled_before is True, out_disabled_before)
    C.check("B0b 初始 #vt-zoom-in 未停用（尚未到 ZOOM_MAX）", in_disabled_before is False, in_disabled_before)

    zin = await C.element_rect_center(page, "#vt-zoom-in")
    top = await C.top_element_at(page, zin["cx"], zin["cy"])
    C.check(
        "B1 #vt-zoom-in 中心點 elementFromPoint 命中自己（未被疊層吃掉）",
        top is not None and top.get("id") == "vt-zoom-in",
        top,
    )
    await C.mouse_click(page, zin["cx"], zin["cy"])
    await asyncio.sleep(0.4)

    tracks_after_in = await C.element_rect_center(page, "#vt-tracks")
    ref_after_in = await ref_sub_offset(page, ref_idx)
    width_ratio_in = tracks_after_in["w"] / tracks_before["w"] if tracks_before["w"] else 0
    C.check(
        f"B2 zoom-in 一級後 #vt-tracks 寬度比 ≈{ZOOM_STEP}（{tracks_before['w']:.1f}px→{tracks_after_in['w']:.1f}px）",
        abs(width_ratio_in - ZOOM_STEP) < 0.05,
        round(width_ratio_in, 4),
        ZOOM_STEP,
    )
    if ref_before and ref_before["offsetPx"] > 0.5:
        block_ratio_in = ref_after_in["offsetPx"] / ref_before["offsetPx"]
        C.check(
            f"B3 參考 .vt-sub(idx={ref_idx}) 像素位移比 ≈{ZOOM_STEP}（{ref_before['offsetPx']:.1f}px→{ref_after_in['offsetPx']:.1f}px）",
            abs(block_ratio_in - ZOOM_STEP) < 0.1,
            round(block_ratio_in, 4),
            ZOOM_STEP,
        )
    else:
        # #4：前提不成立時明確 SKIP（不計入 PASS），不再用 check(..., True) 灌水成通過。
        C.skip("B3 參考塊像素位移比", f"參考塊起點太接近 0（offsetPx={ref_before}），無法算有意義的位移比")

    zout = await C.element_rect_center(page, "#vt-zoom-out")
    top2 = await C.top_element_at(page, zout["cx"], zout["cy"])
    C.check(
        "B4 #vt-zoom-out 中心點 elementFromPoint 命中自己",
        top2 is not None and top2.get("id") == "vt-zoom-out",
        top2,
    )
    await C.mouse_click(page, zout["cx"], zout["cy"])
    await asyncio.sleep(0.4)
    tracks_after_out = await C.element_rect_center(page, "#vt-tracks")
    out_disabled_after = await C.js(page, "document.querySelector('#vt-zoom-out')?.disabled")
    C.check(
        f"B5 zoom-out 一級後寬度回到約原寬（{tracks_after_out['w']:.1f}px vs 原 {tracks_before['w']:.1f}px）",
        abs(tracks_after_out["w"] - tracks_before["w"]) < 2.0,
        round(tracks_after_out["w"], 2),
        round(tracks_before["w"], 2),
    )
    C.check("B6 zoom-out 後 #vt-zoom-out 重新停用（回到下限）", out_disabled_after is True, out_disabled_after)

    # 活性檢查（非固定值；#3 更名——這不是「突變測試」，只改輸入不動產品碼）：連按多次
    # zoom-in，寬度要持續變大，證明讀到的是 zoom 驅動的真實值、非某個 CSS 常數。真正的突變
    # 測試（revert 產品碼那行→確認變紅）記在 MUTATIONS.md。
    for _ in range(4):
        b = await C.element_rect_center(page, "#vt-zoom-in")
        if (await C.js(page, "document.querySelector('#vt-zoom-in')?.disabled")):
            break
        await C.mouse_click(page, b["cx"], b["cy"])
        await asyncio.sleep(0.15)
    tracks_mut = await C.element_rect_center(page, "#vt-tracks")
    C.check(
        "B7 活性檢查：連續多次 zoom-in 後 #vt-tracks 寬度持續增加（非死碼/固定值）",
        tracks_mut["w"] > tracks_after_in["w"] * 2,
        round(tracks_mut["w"], 1),
        f">{tracks_after_in['w']*2:.1f}",
    )
    # 收尾：用 __vtSeek 之外的方式把 zoom 收回 1（連按 zoom-out 到停用為止），
    # 避免後續量測疊在放大狀態上；此為設定/收尾動作非受測互動，可用 UI 按鈕。
    for _ in range(10):
        disabled = await C.js(page, "document.querySelector('#vt-zoom-out')?.disabled")
        if disabled:
            break
        b = await C.element_rect_center(page, "#vt-zoom-out")
        await C.mouse_click(page, b["cx"], b["cy"])
        await asyncio.sleep(0.1)

    # ============ 2. 播放頭：真事件拖曳刮動 ============
    ready = await C.js(
        page,
        "(() => { const v = document.querySelector('#vt-video'); "
        "return { readyState: v.readyState, seekableLen: v.seekable.length, duration: v.duration }; })()",
    )
    C.check(
        "C0 刮動前提：#vt-video readyState>=1 且 seekable.length>0",
        (ready or {}).get("readyState", 0) >= 1 and (ready or {}).get("seekableLen", 0) > 0,
        ready,
    )

    grip = await C.element_rect_center(page, "#vt-playhead-grip")
    top3 = await C.top_element_at(page, grip["cx"], grip["cy"])
    C.check(
        "C1 #vt-playhead-grip 中心點 elementFromPoint 命中自己或其子節點（未被疊層吃掉）",
        top3 is not None and (top3.get("id") == "vt-playhead-grip"),
        top3,
    )

    tl_rect = await C.element_rect_center(page, "#vt-timeline")
    before_seek = await C.js(page, "document.querySelector('#vt-video').currentTime")
    # 拖到時間軸 70% 處（真事件 down→多次 move→up；不用 __vtSeek 驅動）
    target_x = tl_rect["x"] + tl_rect["w"] * 0.7
    target_y = grip["cy"]
    await C.mouse_drag(page, grip["cx"], grip["cy"], target_x, target_y, steps=10, settle=0.05)
    await asyncio.sleep(0.4)
    after_seek = await C.js(page, "document.querySelector('#vt-video').currentTime")
    duration = (ready or {}).get("duration") or 1
    expect_time = 0.7 * duration
    C.check(
        f"C2 拖曳播放頭把手到 70% 處後 currentTime 對到約 {expect_time:.2f}s",
        abs((after_seek or -999) - expect_time) < max(1.0, duration * 0.08),
        after_seek,
        round(expect_time, 2),
    )
    C.check(
        "C3 拖曳前後 currentTime 確實改變（非靜默失效）",
        abs((after_seek or 0) - (before_seek or 0)) > 0.5,
        {"before": before_seek, "after": after_seek},
    )
    ph_left_1 = await C.js(page, "document.querySelector('#vt-playhead')?.style?.left")

    # 活性檢查（#3 更名）：拖到另一個不同位置（30%），播放頭 left 與 currentTime 要再變一次。
    grip2 = await C.element_rect_center(page, "#vt-playhead-grip")
    # #5：第二次刮動也是一次真事件拖曳（起點是另一次點擊），補疊層驗證（原本只驗了 C1 首擊）。
    top_grip2 = await C.top_element_at(page, grip2["cx"], grip2["cy"])
    C.check(
        "C3b #vt-playhead-grip 第二次刮動起點 elementFromPoint 命中把手（未被疊層吃掉）",
        top_grip2 is not None and top_grip2.get("id") == "vt-playhead-grip",
        top_grip2,
    )
    target_x2 = tl_rect["x"] + tl_rect["w"] * 0.3
    await C.mouse_drag(page, grip2["cx"], grip2["cy"], target_x2, grip2["cy"], steps=10, settle=0.05)
    await asyncio.sleep(0.4)
    ph_left_2 = await C.js(page, "document.querySelector('#vt-playhead')?.style?.left")
    after_seek2 = await C.js(page, "document.querySelector('#vt-video').currentTime")
    C.check(
        "C4 活性檢查：再拖到不同位置後 #vt-playhead left 與 currentTime 都跟著再變一次",
        ph_left_2 != ph_left_1 and abs((after_seek2 or 0) - (after_seek or 0)) > 0.5,
        {"left1": ph_left_1, "left2": ph_left_2, "t1": after_seek, "t2": after_seek2},
    )

    # ============ 3. 波形：canvas 有實際畫 ============
    async def waveform_painted():
        return await C.js(
            page,
            """(() => {
              const c = document.querySelector('#vt-waveform');
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
        "D1 #vt-waveform canvas 有實際畫（非零 alpha 像素數 > 0）",
        bool(wf and wf.get("painted")),
        wf,
    )

    # ============ 4. 標題卡自動分軌（assignCardLanes）============
    # __vtDropTpl 僅作「佈設」用（等同拖版型到軌道的程式化版本），不是受測互動本身；
    # 受測的是 assignCardLanes() 算出來、反映在 #vt-card-track 高度上的車道數。
    h0 = await C.js(page, "window.__vtCardTrackHeight ? window.__vtCardTrackHeight() : null")
    C.check("E0 初始標題卡軌高度 = 44px（0 張卡、effLanes 最少為 1）", h0 == 44, h0, 44)

    c1 = await C.js(page, "window.__vtDropTpl('big', 2)")
    h1 = await C.js(page, "window.__vtCardTrackHeight()")
    C.check("E1 佈設 1 張標題卡（不重疊）後軌高度仍為 44px（1 車道）", h1 == 44, h1, 44)

    c2 = await C.js(page, "window.__vtDropTpl('lower', 3)")  # 2-5 與 3-6 重疊
    h2 = await C.js(page, "window.__vtCardTrackHeight()")
    C.check(
        "E2 佈設第 2 張與第 1 張時間重疊後軌高度變 80px（2 車道：2*36+2*4）",
        h2 == 80,
        h2,
        80,
    )
    C.check("E2b 兩張卡確實建立成功（__vtDropTpl 回傳非 null）", c1 is not None and c2 is not None, {"c1": c1, "c2": c2})

    # 活性檢查（非固定值；#3 更名——只改輸入不動產品碼）：把第 2 張卡挪到不重疊時間，
    # 車道數應收回 1（44px），證明高度是 assignCardLanes 實算、非寫死 80px。
    if c2:
        await C.js(page, f"window.__vtSetCard && (window.__vtSelectCard({c2['id']}), window.__vtSetCard({{start: 15, end: 18}}))")
    h3 = await C.js(page, "window.__vtCardTrackHeight()")
    C.check("E3 活性檢查：把重疊卡挪開後軌高度收回 44px（非固定 80px）", h3 == 44, h3, 44)

    # ============ 5. 拖某張卡端點（改出點）後起訖有變（真事件拖曳）============
    # 為了讓把手可見（renderCardTrack 要求 wpx>=24 才畫 .vt-card-h），先確保卡夠寬：
    # 用 __vtDropTpl 佈設一張夠長的卡（6 秒），必要時 UI 再放大一級。
    if c1:
        await C.js(page, f"window.__vtSelectCard({c1['id']}); window.__vtSetCard({{start: 1, end: 7}})")
    cards_before = await C.js(page, "window.__vtCards()")

    handle = await C.element_rect_center(page, "#vt-card-track .vt-card .vt-card-h.is-r")
    if handle is None:
        # 把手沒畫出來（太窄）：放大一級時間軸再試一次
        zb = await C.element_rect_center(page, "#vt-zoom-in")
        await C.mouse_click(page, zb["cx"], zb["cy"])
        await asyncio.sleep(0.3)
        handle = await C.element_rect_center(page, "#vt-card-track .vt-card .vt-card-h.is-r")
    C.check("F0 找到卡片右端點把手 .vt-card-h.is-r（renderCardTrack wpx>=24 有畫出來）", handle is not None, handle)

    if handle:
        top4 = await C.top_element_at(page, handle["cx"], handle["cy"])
        C.check(
            "F1 端點把手中心點 elementFromPoint 命中該把手（未被卡片本體蓋掉）",
            top4 is not None and "vt-card-h" in (top4.get("cls") or ""),
            top4,
        )
        # 真事件拖曳：把出點往右拖一段距離（沿時間軸水平方向）
        drag_dx = 60
        await C.mouse_drag(
            page, handle["cx"], handle["cy"], handle["cx"] + drag_dx, handle["cy"], steps=8, settle=0.05
        )
        await asyncio.sleep(0.3)
        cards_after = await C.js(page, "window.__vtCards()")
        before_card = next((c for c in (cards_before or []) if c["id"] == c1["id"]), None) if c1 else None
        after_card = next((c for c in (cards_after or []) if c["id"] == c1["id"]), None) if c1 else None
        C.check(
            "F2 拖曳右端點把手後該卡的 end 有變（起點不變、訖點變長）",
            bool(before_card and after_card and after_card["end"] != before_card["end"] and after_card["start"] == before_card["start"]),
            {"before": before_card, "after": after_card},
        )
    else:
        C.check("F1 端點把手中心點 elementFromPoint 命中該把手（略過：F0 未找到把手）", False, None)
        C.check("F2 拖曳右端點把手後該卡的 end 有變（略過：F0 未找到把手）", False, None)

    OUT.write_text(
        json.dumps({"mode": "video-edit-prototype", "results": C.results_as_dict()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    bad = C.summary()
    await ws.close()
    return len(bad) == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
