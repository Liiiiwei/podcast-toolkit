#!/usr/bin/env python3
"""獨立驗收走查：編輯器「顯示標題卡軌」UI 開關（#cardtrack-toggle）可操作 + 持久化。

由 fresh-context 驗收者撰寫，不信任「我已接好」。只依原始需求（layout_mode 前端 UI
可操作、狀態存得下重載保持）＋待驗檔案（index.html / app.js / api.js）判斷。

每個 ✓ 綁布林斷言 ok=實得==期待；真事件點擊（Input.dispatchMouseEvent down→up）；
可點元素先驗 elementFromPoint 最上層是該 checkbox；round-trip 走真存檔 + 真重載。

前提：serve_podcast.py 起 :8795（服務 live working tree、config 指向沙盒兩集）、
headless Chrome CDP 在 :9331。跑法：/usr/bin/python3 -u verify_cardtrack_toggle.py [t12|t3|t4|t5|all]

沙盒兩集：
  PRIM = .../20260601 時間軸基準集（測試中會被切 podcast/video）
  SEC  = .../20260602 第二集podcast（永遠 podcast，供換集殘留測試）
"""
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp_common as C

POD = "http://127.0.0.1:8795/?pthook=1"
PRIM = "/private/tmp/pt-timeline-baseline/episode/20260601 時間軸基準集"
SEC = "/private/tmp/pt-timeline-baseline/episode/20260602 第二集podcast"


async def ws_url():
    with urllib.request.urlopen("http://127.0.0.1:9331/json/version", timeout=5) as r:
        return json.loads(r.read())["webSocketDebuggerUrl"]


def api_save(layout_mode, episode_dir):
    """直接打 /api/save 設定某集的 layout_mode（用於佈置測試初始態，非受測路徑）。"""
    body = json.dumps({"layout_mode": layout_mode, "episode_dir": episode_dir}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8795/api/save", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


def api_switch(path):
    body = json.dumps({"path": path}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8795/api/episode/switch", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


async def wait_for(page, expr, timeout=25, interval=0.4):
    for _ in range(int(timeout / interval)):
        v = await C.js(page, expr)
        if v:
            return v
        await asyncio.sleep(interval)
    return None


async def open_pod(browser, ws, sessions):
    page = await C.open_page(browser, ws, sessions, POD, settle=2.0)
    ready = await wait_for(
        page,
        "document.querySelector('#card-timeline') && "
        "document.querySelectorAll('#card-timeline .tl-block').length > 0",
    )
    return page, ready


async def track_state(page):
    return await C.js(
        page,
        """(() => {
          const t = document.querySelector('#tl-card-track');
          if (!t) return null;
          const r = t.getBoundingClientRect();
          return { hidden: t.hidden, height: r.height, offsetHeight: t.offsetHeight };
        })()""",
    )


async def toggle_state(page):
    return await C.js(
        page,
        """(() => {
          const c = document.querySelector('#cardtrack-toggle');
          if (!c) return null;
          const r = c.getBoundingClientRect();
          return { checked: c.checked, cx: r.left+r.width/2, cy: r.top+r.height/2, w: r.width };
        })()""",
    )


async def unsaved_badge(page):
    return await C.js(
        page,
        """(() => {
          const b = document.querySelector('#unsaved-badge');
          const cnt = document.querySelector('#unsaved-count');
          return { badgeHidden: b ? b.classList.contains('hidden') : null,
                   count: cnt ? cnt.textContent : null };
        })()""",
    )


async def real_click(page, selector):
    """真事件點擊某元素中心，先驗 elementFromPoint 最上層命中它（回傳 top 描述）。"""
    rect = await C.element_rect_center(page, selector)
    if not rect:
        return None, None
    top = await C.top_element_at(page, rect["cx"], rect["cy"])
    await C.mouse_click(page, rect["cx"], rect["cy"])
    await asyncio.sleep(0.4)
    return rect, top


async def click_save_and_wait(page):
    """真事件點 #save-btn，輪詢等到儲存完成（badge 歸零 or 按鈕回復）。"""
    await real_click(page, "#save-btn")
    # postSave→loadEpisodeState→renderCards；等 outputDirty 清掉後 badge 歸零
    await wait_for(
        page,
        "(() => { const b=document.querySelector('#unsaved-badge'); "
        "return b && b.classList.contains('hidden'); })()",
        timeout=15,
    )
    await asyncio.sleep(0.5)


# ============================================================ 版本指紋
async def fingerprint(page):
    return await C.js(page, "!!document.querySelector('#cardtrack-toggle')")


# ============================================================ T1 / T2 顯隱
async def t12(browser, ws, sessions):
    print("\n---- T1/T2 真事件勾選/取消 → 標題卡軌顯隱 ----", flush=True)
    api_save("podcast", PRIM)  # 佈置：podcast 初始態
    page, ready = await open_pod(browser, ws, sessions)
    fp = await fingerprint(page)
    if not fp:
        C.check("版本指紋 #cardtrack-toggle 存在（來源同步）", False, fp)
        return
    C.check("T0 時間軸已渲染 + #cardtrack-toggle 存在", bool(ready) and fp, {"ready": bool(ready), "fp": fp})

    ts0 = await toggle_state(page)
    tr0 = await track_state(page)
    C.check("T1.0 初始：checkbox 未勾 + 軌隱藏（hidden 且 height==0）",
            ts0 and ts0["checked"] is False and tr0["hidden"] is True and tr0["height"] == 0,
            {"toggle": ts0, "track": tr0})

    # 真事件勾選（先驗 elementFromPoint 命中 checkbox）
    rect, top = await real_click(page, "#cardtrack-toggle")
    C.check("T1.1 checkbox 中心 elementFromPoint 命中它本身（未被疊層吃掉）",
            top is not None and top.get("id") == "cardtrack-toggle", top)
    ts1 = await toggle_state(page)
    tr1 = await track_state(page)
    C.check("T1.2 勾選後：checkbox.checked===true", ts1 and ts1["checked"] is True, ts1)
    C.check("T1.3 勾選後：#tl-card-track 顯示（hidden===false 且 height>0）",
            tr1 and tr1["hidden"] is False and tr1["height"] > 0, tr1)

    # 真事件取消
    await C.mouse_click(page, rect["cx"], rect["cy"])
    await asyncio.sleep(0.4)
    ts2 = await toggle_state(page)
    tr2 = await track_state(page)
    C.check("T2.1 取消後：checkbox.checked===false", ts2 and ts2["checked"] is False, ts2)
    C.check("T2.2 取消後：軌隱藏不佔位（hidden===true 且 height==0）",
            tr2 and tr2["hidden"] is True and tr2["height"] == 0, tr2)
    errs = C.console_errors(page)
    C.check("T1/T2 全程無 console 例外", len(errs) == 0, errs)


# ============================================================ T3 round-trip
async def t3(browser, ws, sessions):
    print("\n---- T3 round-trip：勾選→存檔→重載保持；取消→存檔→重載保持 ----", flush=True)
    api_save("podcast", PRIM)  # 佈置：podcast 起點
    page, ready = await open_pod(browser, ws, sessions)
    if not await fingerprint(page):
        C.check("版本指紋", False); return
    ts0 = await toggle_state(page)
    C.check("T3.0 起點 checkbox 未勾", ts0 and ts0["checked"] is False, ts0)

    # 勾選 → 存檔
    await real_click(page, "#cardtrack-toggle")
    await click_save_and_wait(page)
    # 重載（真重載：全新頁面）
    page2, _ = await open_pod(browser, ws, sessions)
    ts_r1 = await toggle_state(page2)
    tr_r1 = await track_state(page2)
    C.check("T3.1 勾選→存檔→重載：checkbox.checked===true（layout_mode 寫進 yaml 又讀回）",
            ts_r1 and ts_r1["checked"] is True, ts_r1)
    C.check("T3.2 勾選→存檔→重載：#tl-card-track height>0", tr_r1 and tr_r1["height"] > 0, tr_r1)

    # 取消 → 存檔 → 重載
    await real_click(page2, "#cardtrack-toggle")
    await click_save_and_wait(page2)
    page3, _ = await open_pod(browser, ws, sessions)
    ts_r2 = await toggle_state(page3)
    tr_r2 = await track_state(page3)
    C.check("T3.3 取消→存檔→重載：checkbox.checked===false", ts_r2 and ts_r2["checked"] is False, ts_r2)
    C.check("T3.4 取消→存檔→重載：#tl-card-track height==0", tr_r2 and tr_r2["height"] == 0, tr_r2)

    # 交叉證據：直接讀 yaml 確認持久層
    api_save("video", PRIM)  # 再寫一次 video 觀察 yaml
    yaml_txt = Path(PRIM + "/episode.yaml").read_text(encoding="utf-8")
    C.check("T3.5 yaml 落地：episode.yaml 含 layout_mode: video", "layout_mode: video" in yaml_txt,
            [l for l in yaml_txt.splitlines() if "layout_mode" in l])
    api_save("podcast", PRIM)  # 還原


# ============================================================ T4 髒標記
async def t4(browser, ws, sessions):
    print("\n---- T4 髒標記：toggle→未存徽章亮；存檔→歸零 ----", flush=True)
    api_save("podcast", PRIM)
    page, ready = await open_pod(browser, ws, sessions)
    if not await fingerprint(page):
        C.check("版本指紋", False); return
    b0 = await unsaved_badge(page)
    C.check("T4.0 起點徽章隱藏（無未存變更）", b0 and b0["badgeHidden"] is True, b0)
    await real_click(page, "#cardtrack-toggle")
    b1 = await unsaved_badge(page)
    C.check("T4.1 toggle 後徽章亮（unsavedCount>0）",
            b1 and b1["badgeHidden"] is False and (b1["count"] or "0").isdigit() and int(b1["count"]) > 0, b1)
    await click_save_and_wait(page)
    b2 = await unsaved_badge(page)
    C.check("T4.2 存檔後徽章歸零（hidden）", b2 and b2["badgeHidden"] is True, b2)
    api_save("podcast", PRIM)  # 還原


# ============================================================ T5 跨集無殘留
async def t5(browser, ws, sessions):
    # 本頁沒有頁內換集 UI（index.html 無 #ep-switch-btn/#ep-switch-menu，
    # switchEpisode 因取用不存在的 #ep-switch-btn 會直接丟例外）→ 產品裡每次換集
    # 都是「從 dashboard 重新整頁載入」。所以跨集正確性＝各集各自整頁載入時，
    # checkbox 依該集 layout_mode 正確初始化（app.js:2564-2568 的載入同步）。
    # T5.0 載 video 集（勾＝載入同步把 default-unchecked 改 checked，可被突變打紅）；
    # T5.1 載 podcast 集（未勾＋軌隱＝載 podcast 集不殘留舊集 video 值，即原始需求 #5）。
    print("\n---- T5 跨集無殘留：各集整頁載入依 layout_mode 正確初始化 ----", flush=True)
    api_switch(PRIM)
    api_save("video", PRIM)      # 主集 video（active=PRIM，200）
    page, ready = await open_pod(browser, ws, sessions)
    if not await fingerprint(page):
        C.check("版本指紋", False); return
    ts0 = await toggle_state(page)
    tr0 = await track_state(page)
    C.check("T5.0 載入 video 集：checkbox 已勾 + 軌顯示（載入同步把預設未勾改為勾）",
            ts0 and ts0["checked"] is True and tr0["height"] > 0, {"toggle": ts0, "track": tr0})

    # 切到 SEC（podcast，建立時即無 layout_mode → 預設 podcast），整頁重新載入
    api_switch(SEC)
    page2, ready2 = await open_pod(browser, ws, sessions)
    ts1 = await toggle_state(page2)
    tr1 = await track_state(page2)
    C.check("T5.1 載入 podcast 集：checkbox 未勾（無殘留舊集 video 值）",
            ts1 and ts1["checked"] is False, ts1)
    C.check("T5.2 載入 podcast 集：軌隱藏（hidden 且 height==0）",
            tr1 and tr1["hidden"] is True and tr1["height"] == 0, tr1)
    # 還原：active 回主集、主集回 podcast
    api_switch(PRIM)
    api_save("podcast", PRIM)


ITEMS = {"t12": t12, "t3": t3, "t4": t4, "t5": t5}


async def main():
    which = sys.argv[1:] or ["all"]
    if which == ["all"]:
        which = list(ITEMS)
    url = await ws_url()
    browser, sessions, ws = await C.connect(url)
    try:
        for k in which:
            await ITEMS[k](browser, ws, sessions)
    finally:
        await ws.close()
    bad = C.summary()
    Path(__file__).with_name("verify_cardtrack_toggle_result.json").write_text(
        json.dumps(C.results_as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    asyncio.run(main())
