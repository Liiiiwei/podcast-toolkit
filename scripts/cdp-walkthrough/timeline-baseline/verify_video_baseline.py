#!/usr/bin/env python3
"""影片模式（video-edit-prototype）時間軸走查基準（W2/W3 前的煙霧測試基準）。

依 CLAUDE.md「下次要動編輯器大功能：先拆 app.js…動刀前先補 Playwright 煙霧測試」
與 README/MUTATIONS.md 的斷言紀律：
  - 每個 ✓ 綁布林斷言 ok=實得==期待，不印值不斷言。
  - 拖曳一律完整 down→多次 move→up（mouse_drag）。
  - 可點元素驗 elementFromPoint 最上層。
  - seek 前先斷言 seekable.length>0。
  - 開頭先驗版本指紋，避免測到未同步的舊碼。
  - 至少 2 項含「真突變」：暫時把產品碼改回舊 bug 行為→對應斷言變紅→還原。

只新增本檔，不永久修改任何產品碼（突變測試在 try/finally 內改回）。

跑法（本機路徑僅供參考，實際 port 依環境而定）：
  ROOT=<repo>/podcast_toolkit/web/static PORT=8796 python3 -u range_server.py &
  /Applications/Google Chrome.app/Contents/MacOS/Google Chrome \
    --headless=new --remote-debugging-port=9522 --user-data-dir=<全新目錄> &
  CDP_PORT=9522 DEMO_PORT=8796 python3 -u verify_video_baseline.py
"""
import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

import cdp_common as C

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent.parent
PROD_JS = REPO_ROOT / "podcast_toolkit" / "web" / "static" / "video-edit-prototype.js"

CDP_PORT = int(os.environ.get("CDP_PORT", "9522"))
DEMO_PORT = int(os.environ.get("DEMO_PORT", "8796"))
URL = f"http://127.0.0.1:{DEMO_PORT}/video-edit-prototype.html?demo=1"

RESULT_JSON = HERE / "verify_video_baseline_result.json"
LOG_TXT = HERE / "verify_video_baseline_log.txt"


def get_cdp_ws_url(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5) as r:
        return json.loads(r.read())["webSocketDebuggerUrl"]


async def wait_for(page, expr, timeout=10, interval=0.2, desc=""):
    """輪詢直到 expr 評估為 truthy 或逾時；回傳最後一次的值（可能是 falsy）。"""
    t = 0.0
    val = None
    while t < timeout:
        val = await C.js(page, expr)
        if val:
            return val
        await asyncio.sleep(interval)
        t += interval
    return val


async def reload(page, url, settle=1.2):
    await page.send("Page.navigate", {"url": url})
    for _ in range(60):
        rs = await C.js(page, "document.readyState")
        if rs == "complete":
            break
        await asyncio.sleep(0.2)
    await asyncio.sleep(settle)


def patch_file(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"預期 {path.name} 恰有 1 處命中 {old!r}，實際 {n} 處，拒絕突變")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def raw_eq(name, got, want):
    """突變測試專用：不進 C.results 分母，僅印出並回傳 (ok, got)。
    RED 階段的失敗是刻意要看到的證據，不該污染最終 5 項『全綠』的統計。"""
    ok = got == want
    mark = "PASS" if ok else "FAIL"
    print(f"    [{mark}] {name}  got={got!r} want={want!r}", flush=True)
    return ok, got


async def main():
    ws_url = get_cdp_ws_url(CDP_PORT)
    browser, sessions, ws = await C.connect(ws_url)
    page = await C.open_page(browser, ws, sessions, URL, settle=2.0)

    log_lines = []

    def logp(s=""):
        print(s, flush=True)
        log_lines.append(str(s))

    logp("=" * 70)
    logp(f"影片模式時間軸走查基準  URL={URL}  CDP={CDP_PORT}")
    logp("=" * 70)

    # ── V1：版本指紋 / 來源同步 ──────────────────────────────────
    logp("\n--- V1 版本指紋 / 來源同步 ---")
    title = await C.js(page, "document.title")
    C.check("V1.1 頁面標題", title == "影片模式 · 時間軸線性剪輯（原型）", title,
            "影片模式 · 時間軸線性剪輯（原型）")

    badge = await C.js(page, "(document.getElementById('vt-mode-badge')||{}).textContent")
    # demo 模式會在徽章附加「· demo」提示（見 video-edit-prototype.js 的 demo 分支），
    # 屬正常行為而非缺陷，故驗證前綴而非全字串。
    C.check("V1.2 模式徽章文字（demo 模式含「· demo」後綴屬正常）",
            (badge or "").strip().startswith("原型"), badge, "以「原型」開頭")

    adv_summary = await C.js(
        page, "(document.querySelector('#vt-adv summary')||{}).textContent"
    )
    C.check("V1.3 進階面板標題", (adv_summary or "").strip() == "進階：字幕樣式",
            adv_summary, "進階：字幕樣式")

    # 先 typeof 確認測試 hook 都是函式，才能安心呼叫（README 踩坑：物件當函式呼叫會靜默回 None）
    hook_types = await wait_for(
        page,
        """(() => ({
            stats: typeof window.__vtStats,
            addCut: typeof window.__vtAddCut,
            style: typeof window.__vtStyle,
            preview: typeof window.__vtPreview,
            setStyle: typeof window.__vtSetStyle,
        }))()""",
        timeout=15,
        desc="等待 CDP 測試掛鉤就緒",
    )
    want_hooks = {"stats": "function", "addCut": "function", "style": "function",
                  "preview": "function", "setStyle": "function"}
    C.check("V1.4 CDP 測試掛鉤皆為函式（typeof 先查再用）", hook_types == want_hooks,
            hook_types, want_hooks)

    duration = await wait_for(page, "window.__vt && window.__vt.duration", timeout=15,
                               desc="等待 demo 影片 metadata 載入")
    C.check("V1.5 demo 影片已載入（duration>0）", bool(duration) and duration > 0, duration, ">0")

    errs = C.console_errors(page)
    C.check("V1.6 頁面載入無 console 例外", errs == [], errs, [])

    # ── V2：波形已畫出 ＋ zoom 幾何數值 ──────────────────────────
    logp("\n--- V2 波形 + zoom 幾何 ---")
    pixel_count = await C.js(
        page,
        """(() => {
          const c = document.getElementById('vt-waveform');
          if (!c || !c.width || !c.height) return 0;
          const ctx = c.getContext('2d');
          const data = ctx.getImageData(0, 0, c.width, c.height).data;
          let n = 0;
          for (let i = 3; i < data.length; i += 4) if (data[i] > 0) n++;
          return n;
        })()""",
    )
    C.check("V2.1 波形 canvas 有畫出非透明像素", (pixel_count or 0) > 0, pixel_count, ">0")

    w0 = (await C.element_rect_center(page, "#vt-tracks"))["w"]

    zin_rect = await C.element_rect_center(page, "#vt-zoom-in")
    top = await C.top_element_at(page, zin_rect["cx"], zin_rect["cy"])
    C.check("V2.2 zoom-in 按鈕未被遮擋（elementFromPoint）", top and top.get("id") == "vt-zoom-in",
            top, {"id": "vt-zoom-in"})
    await C.mouse_click(page, zin_rect["cx"], zin_rect["cy"])
    await asyncio.sleep(0.15)
    w1 = (await C.element_rect_center(page, "#vt-tracks"))["w"]
    ratio1 = round(w1 / w0, 3) if w0 else None
    C.check("V2.3 zoom-in 一次：#vt-tracks 寬度放大 ≈1.6×", ratio1 is not None and abs(ratio1 - 1.6) < 0.02,
            ratio1, 1.6)

    zin_rect2 = await C.element_rect_center(page, "#vt-zoom-in")
    await C.mouse_click(page, zin_rect2["cx"], zin_rect2["cy"])
    await asyncio.sleep(0.15)
    w2 = (await C.element_rect_center(page, "#vt-tracks"))["w"]
    ratio2 = round(w2 / w0, 3) if w0 else None
    C.check("V2.4 zoom-in 兩次：#vt-tracks 寬度放大 ≈2.56×（1.6²）",
            ratio2 is not None and abs(ratio2 - 2.56) < 0.03, ratio2, 2.56)

    zout_disabled = await C.js(page, "document.getElementById('vt-zoom-out').disabled")
    C.check("V2.5 已放大後 zoom-out 按鈕可用（未 disabled）", zout_disabled is False, zout_disabled, False)

    zout_rect = await C.element_rect_center(page, "#vt-zoom-out")
    top_out = await C.top_element_at(page, zout_rect["cx"], zout_rect["cy"])
    C.check("V2.6 zoom-out 按鈕未被遮擋（elementFromPoint）", top_out and top_out.get("id") == "vt-zoom-out",
            top_out, {"id": "vt-zoom-out"})
    await C.mouse_click(page, zout_rect["cx"], zout_rect["cy"])
    await asyncio.sleep(0.15)
    zout_rect2 = await C.element_rect_center(page, "#vt-zoom-out")
    await C.mouse_click(page, zout_rect2["cx"], zout_rect2["cy"])
    await asyncio.sleep(0.15)
    w3 = (await C.element_rect_center(page, "#vt-tracks"))["w"]
    ratio3 = round(w3 / w0, 3) if w0 else None
    C.check("V2.7 zoom-out 兩次後寬度回到 1×（往返一致，非死碼）",
            ratio3 is not None and abs(ratio3 - 1.0) < 0.02, ratio3, 1.0)

    # ── V3：播放頭刮動（scrub）──────────────────────────────────
    logp("\n--- V3 播放頭 scrub ---")
    media_state = await C.js(
        page,
        "(() => { const v=document.getElementById('vt-video'); "
        "return {readyState: v.readyState, seekableLen: v.seekable.length}; })()",
    )
    seekable_ok = C.check(
        "V3.1 seek 前置條件：readyState>=1 且 seekable.length>0（Range 伺服器必要條件）",
        media_state and media_state["readyState"] >= 1 and media_state["seekableLen"] > 0,
        media_state, {"readyState": ">=1", "seekableLen": ">0"},
    )

    if seekable_ok:
        grip = await C.element_rect_center(page, "#vt-playhead-grip")
        top_grip = await C.top_element_at(page, grip["cx"], grip["cy"])
        C.check("V3.2 播放頭把手未被遮擋（elementFromPoint）",
                top_grip and top_grip.get("id") == "vt-playhead-grip",
                top_grip, {"id": "vt-playhead-grip"})

        tl_rect = await C.element_rect_center(page, "#vt-timeline")
        x70 = tl_rect["x"] + tl_rect["w"] * 0.70
        await C.mouse_drag(page, grip["cx"], grip["cy"], x70, grip["cy"], steps=8, settle=0.03)
        ct1 = await C.js(page, "document.getElementById('vt-video').currentTime")
        expect1 = duration * 0.70
        C.check("V3.3 拖到 70% 位置：currentTime ≈ 70% duration",
                abs(ct1 - expect1) < max(0.6, duration * 0.03), ct1, round(expect1, 2))

        grip2 = await C.element_rect_center(page, "#vt-playhead-grip")
        x30 = tl_rect["x"] + tl_rect["w"] * 0.30
        await C.mouse_drag(page, grip2["cx"], grip2["cy"], x30, grip2["cy"], steps=8, settle=0.03)
        ct2 = await C.js(page, "document.getElementById('vt-video').currentTime")
        expect2 = duration * 0.30
        C.check("V3.4 再拖到 30% 位置：currentTime ≈ 30% duration",
                abs(ct2 - expect2) < max(0.6, duration * 0.03), ct2, round(expect2, 2))

        C.check("V3.5 兩次拖曳的 currentTime 確實不同（非死碼／固定值）",
                abs(ct1 - ct2) > 0.5, {"ct1": ct1, "ct2": ct2}, "ct1 != ct2")
    else:
        C.skip("V3.2-V3.5 播放頭拖曳", "seekable.length<=0，前提不成立，略過避免假綠")

    # ── V4：剪除段（cuts）→ renderCuts ───────────────────────────
    logp("\n--- V4 cuts → renderCuts ---")
    stats0 = await C.js(page, "window.__vtStats()")
    C.check("V4.1 初始 cutCount==0", stats0["cutCount"] == 0, stats0["cutCount"], 0)

    ok_add1 = await C.js(page, "window.__vtAddCut(2,5)")
    stats1 = await C.js(page, "window.__vtStats()")
    dom_cut_count1 = await C.js(page, "document.querySelectorAll('.vt-cut').length")
    label1 = await C.js(
        page, "(document.querySelector('.vt-cut .vt-cut-label')||{}).textContent"
    )
    C.check("V4.2 __vtAddCut(2,5) 成功且 cutCount==1", ok_add1 is True and stats1["cutCount"] == 1,
            {"ok": ok_add1, "cutCount": stats1["cutCount"]}, {"ok": True, "cutCount": 1})
    C.check("V4.3 DOM .vt-cut 節點數 == 1", dom_cut_count1 == 1, dom_cut_count1, 1)
    C.check("V4.4 剪除段標籤文字正確", label1 == "✕ 3.0s", label1, "✕ 3.0s")

    ok_add2 = await C.js(page, "window.__vtAddCut(4,8)")
    stats2 = await C.js(page, "window.__vtStats()")
    dom_cut_count2 = await C.js(page, "document.querySelectorAll('.vt-cut').length")
    C.check("V4.5 重疊段 __vtAddCut(4,8) 後合併成 [[2,8]]（cutCount 仍為 1）",
            ok_add2 is True and stats2["cuts"] == [[2, 8]] and stats2["cutCount"] == 1,
            {"ok": ok_add2, "cuts": stats2["cuts"], "cutCount": stats2["cutCount"]},
            {"ok": True, "cuts": [[2, 8]], "cutCount": 1})
    C.check("V4.6 合併後 DOM .vt-cut 節點數仍為 1", dom_cut_count2 == 1, dom_cut_count2, 1)

    # 真拖曳：拖右端把手，驗證 bindCutTrim 真的改動了 cuts[0][1]
    handle_r = await C.element_rect_center(page, ".vt-cut-h.is-r")
    if handle_r:
        top_handle = await C.top_element_at(page, handle_r["cx"], handle_r["cy"])
        C.check("V4.7 右端把手未被遮擋（elementFromPoint）",
                top_handle is not None and "vt-cut-h" in (top_handle.get("cls") or ""),
                top_handle, "class 含 vt-cut-h")
        tl_rect2 = await C.element_rect_center(page, "#vt-timeline")
        dx_px = tl_rect2["w"] * 0.05  # 拖動軌道寬度 5% 對應的時間量
        await C.mouse_drag(page, handle_r["cx"], handle_r["cy"],
                            handle_r["cx"] + dx_px, handle_r["cy"], steps=8, settle=0.03)
        stats3 = await C.js(page, "window.__vtStats()")
        expected_end = 8 + (dx_px / tl_rect2["w"]) * duration
        C.check("V4.8 拖右端把手後，剪除段終點確實隨拖曳變化（非固定值）",
                stats3["cuts"] and abs(stats3["cuts"][0][1] - 8) > 0.3,
                stats3["cuts"], f"end != 8（預期≈{round(expected_end,2)}）")
    else:
        C.skip("V4.7-V4.8 拖右端把手", "找不到 .vt-cut-h.is-r（可能寬度不足 26px 未渲染把手）")

    # 真點擊：點剪除段本體（非把手）→ removeCutAt
    stats_before_click = await C.js(page, "window.__vtStats()")
    cut_rect = await C.element_rect_center(page, ".vt-cut")
    # 點在段落左側 30% 處，避開兩端把手
    click_x = cut_rect["x"] + cut_rect["w"] * 0.3
    click_y = cut_rect["cy"]
    top_body = await C.top_element_at(page, click_x, click_y)
    body_ok = top_body is not None and ("vt-cut" in (top_body.get("cls") or "") and
                                         "vt-cut-h" not in (top_body.get("cls") or ""))
    C.check("V4.9 點擊落點命中剪除段本體（非把手，elementFromPoint）", body_ok, top_body, "class 含 vt-cut 不含 vt-cut-h")
    await C.mouse_click(page, click_x, click_y)
    await asyncio.sleep(0.1)
    stats4 = await C.js(page, "window.__vtStats()")
    dom_cut_count4 = await C.js(page, "document.querySelectorAll('.vt-cut').length")
    C.check("V4.10 點擊剪除段本體 → removeCutAt 生效，cutCount 回到 0",
            stats4["cutCount"] == 0 and dom_cut_count4 == 0,
            {"cutCount": stats4["cutCount"], "dom": dom_cut_count4}, {"cutCount": 0, "dom": 0})

    # ── 真突變 #1：addCut 合併邏輯（video-edit-prototype.js 的 addCut，行 213）──
    logp("\n--- MUT1 真突變：addCut 重疊合併判斷（產品碼一行）---")
    mut1_evidence = {"red": None, "green": None}
    OLD1 = "if (cur[0] <= last[1])"
    NEW1 = "if (false)"
    try:
        patch_file(PROD_JS, OLD1, NEW1)
        await reload(page, URL, settle=1.5)
        await wait_for(page, "typeof window.__vtAddCut === 'function'", timeout=15)
        await C.js(page, "window.__vtAddCut(2,5)")
        await C.js(page, "window.__vtAddCut(4,8)")
        stats_red = await C.js(page, "window.__vtStats()")
        red_ok, red_got = raw_eq("MUT1-RED 重疊本應合併成 cutCount==1（突變後預期會錯）",
                                  stats_red["cutCount"], 1)
        mut1_evidence["red"] = stats_red["cuts"]
    finally:
        patch_file(PROD_JS, NEW1, OLD1)

    await reload(page, URL, settle=1.5)
    await wait_for(page, "typeof window.__vtAddCut === 'function'", timeout=15)
    await C.js(page, "window.__vtAddCut(2,5)")
    await C.js(page, "window.__vtAddCut(4,8)")
    stats_green = await C.js(page, "window.__vtStats()")
    green_ok, green_got = raw_eq("MUT1-GREEN 還原後重疊合併恢復 cutCount==1",
                                  stats_green["cutCount"], 1)
    mut1_evidence["green"] = stats_green["cuts"]

    C.check(
        "MUT1 真突變成立：改回舊行為→cutCount 錯誤變 2（RED），還原→恢復 1（GREEN）",
        (red_ok is False) and (green_ok is True),
        mut1_evidence,
        {"red_cuts": "[[2,5],[4,8]]（未合併）", "green_cuts": "[[2,8]]（合併）"},
    )

    # 清空狀態，重新整頁進入 V5（乾淨的預設樣式起點）
    await reload(page, URL, settle=2.0)
    await wait_for(page, "typeof window.__vtStyle === 'function'", timeout=15)

    # ── V5：字幕樣式面板 ────────────────────────────────────────
    logp("\n--- V5 字幕樣式面板 ---")
    style0 = await C.js(page, "window.__vtStyle()")
    preview0 = await C.js(page, "window.__vtPreview()")
    C.check("V5.1 預設樣式 bold==1 且預覽 fontWeight==700（前置態）",
            style0["bold"] == 1 and preview0["fontWeight"] == "700",
            {"bold": style0["bold"], "fontWeight": preview0["fontWeight"]},
            {"bold": 1, "fontWeight": "700"})

    adv_rect = await C.element_rect_center(page, "#vt-adv summary")
    top_summary = await C.top_element_at(page, adv_rect["cx"], adv_rect["cy"])
    C.check("V5.2 進階面板 summary 未被遮擋（elementFromPoint）",
            top_summary is not None and top_summary.get("tag") == "SUMMARY",
            top_summary, {"tag": "SUMMARY"})
    await C.mouse_click(page, adv_rect["cx"], adv_rect["cy"])
    await asyncio.sleep(0.15)
    adv_open = await C.js(page, "document.getElementById('vt-adv').open")
    C.check("V5.3 點擊 summary 後面板展開（details.open==true）", adv_open is True, adv_open, True)

    bold_off_rect = await C.element_rect_center(page, '#st-bold button[data-v="0"]')
    top_bold_off = await C.top_element_at(page, bold_off_rect["cx"], bold_off_rect["cy"])
    C.check("V5.4「關」按鈕未被遮擋（elementFromPoint）",
            top_bold_off is not None and top_bold_off.get("tag") == "BUTTON",
            top_bold_off, {"tag": "BUTTON"})
    await C.mouse_click(page, bold_off_rect["cx"], bold_off_rect["cy"])
    await asyncio.sleep(0.15)
    style1 = await C.js(page, "window.__vtStyle()")
    preview1 = await C.js(page, "window.__vtPreview()")
    C.check("V5.5 點擊粗體「關」→ state.style.bold==0 且預覽 fontWeight==400",
            style1["bold"] == 0 and preview1["fontWeight"] == "400",
            {"bold": style1["bold"], "fontWeight": preview1["fontWeight"]},
            {"bold": 0, "fontWeight": "400"})

    align_rect = await C.element_rect_center(page, '#st-align button[data-v="10"]')
    top_align = await C.top_element_at(page, align_rect["cx"], align_rect["cy"])
    C.check("V5.6「正中」對齊按鈕未被遮擋（elementFromPoint）",
            top_align is not None and top_align.get("tag") == "BUTTON",
            top_align, {"tag": "BUTTON"})
    await C.mouse_click(page, align_rect["cx"], align_rect["cy"])
    await asyncio.sleep(0.15)
    style2 = await C.js(page, "window.__vtStyle()")
    preview_top = await C.js(page, "document.getElementById('vt-sub-preview').style.top")
    C.check("V5.7 點擊「正中」→ state.style.alignment==10 且預覽 wrap.style.top==='50%'（直接 DOM 檢查）",
            style2["alignment"] == 10 and preview_top == "50%",
            {"alignment": style2["alignment"], "top": preview_top},
            {"alignment": 10, "top": "50%"})

    # 真拖曳：拖字級滑桿到約 100（min24/max140），驗證數值面板與預覽同步
    size_rect = await C.element_rect_center(page, "#st-size")
    frac = (100 - 24) / (140 - 24)
    target_x = size_rect["x"] + size_rect["w"] * frac
    await C.mouse_drag(page, size_rect["cx"], size_rect["cy"], target_x, size_rect["cy"],
                        steps=10, settle=0.03)
    await asyncio.sleep(0.15)
    size_val_text = await C.js(page, "document.getElementById('st-size-val').textContent")
    size_input_val = await C.js(page, "Number(document.getElementById('st-size').value)")
    preview2 = await C.js(page, "window.__vtPreview()")
    size_changed = size_input_val is not None and abs(size_input_val - 60) > 5
    label_matches = str(size_val_text) == str(size_input_val)
    C.check("V5.8 拖字級滑桿：數值標籤與 input.value 同步、且偏離預設 60（真拖曳生效）",
            size_changed and label_matches,
            {"input": size_input_val, "label": size_val_text}, {"input": "≈100（!=60）", "label": "同 input"})
    C.check("V5.9 拖字級滑桿後預覽字級 px 值有變化（非死碼）",
            preview2["fontSize"] != preview0["fontSize"],
            preview2["fontSize"], f"!= {preview0['fontSize']}")

    # ── 真突變 #2：applyStyle 的 bold→fontWeight 綁定（產品碼一行）──
    logp("\n--- MUT2 真突變：applyStyle 的 bold→fontWeight 綁定 ---")
    mut2_evidence = {"red": None, "green": None}
    OLD2 = 'el.style.fontWeight = st.bold ? "700" : "400";'
    NEW2 = 'el.style.fontWeight = "700";'
    try:
        patch_file(PROD_JS, OLD2, NEW2)
        await reload(page, URL, settle=1.5)
        await wait_for(page, "typeof window.__vtSetStyle === 'function'", timeout=15)
        p_red = await C.js(page, "window.__vtSetStyle({bold: 0})")
        preview_red = await C.js(page, "window.__vtPreview()")
        red2_ok, red2_got = raw_eq("MUT2-RED bold=0 應反映 fontWeight==400（突變後預期會錯，仍卡在 700）",
                                    preview_red["fontWeight"], "400")
        mut2_evidence["red"] = preview_red["fontWeight"]
    finally:
        patch_file(PROD_JS, NEW2, OLD2)

    await reload(page, URL, settle=1.5)
    await wait_for(page, "typeof window.__vtSetStyle === 'function'", timeout=15)
    p_green = await C.js(page, "window.__vtSetStyle({bold: 0})")
    preview_green = await C.js(page, "window.__vtPreview()")
    green2_ok, green2_got = raw_eq("MUT2-GREEN 還原後 bold=0 → fontWeight 恢復 400",
                                    preview_green["fontWeight"], "400")
    mut2_evidence["green"] = preview_green["fontWeight"]

    C.check(
        "MUT2 真突變成立：改回舊行為→fontWeight 錯誤卡在 700（RED），還原→恢復 400（GREEN）",
        (red2_ok is False) and (green2_ok is True),
        mut2_evidence,
        {"red_fontWeight": "700（未跟隨 bold=0）", "green_fontWeight": "400"},
    )

    bad = C.summary()

    RESULT_JSON.write_text(
        json.dumps({"mode": "video-edit-prototype-baseline", "results": C.results_as_dict()},
                    ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    LOG_TXT.write_text("\n".join(log_lines), encoding="utf-8")

    await ws.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
