#!/usr/bin/env python3
# CDP 走查：驗這一包四件事的三個前端面（UI 入口 / 真模式載真字幕 / 確認框不分模式都跳）。
# 紀律：每個 ✓ 都綁布林斷言；每一項都配「把那一半關掉會紅」的突變測試（README:68-77）。
import asyncio, json, sys

import websockets

CDP = sys.argv[1]
BASE = "http://127.0.0.1:8792"
PROTO = f"{BASE}/static/video-edit-prototype.html"

results = []


def check(name, ok, got=None, want=None):
    results.append((name, bool(ok)))
    mark = "✓" if ok else "✗"
    extra = "" if got is None else f"  got={got!r} want={want!r}"
    print(f"{mark} {name}{extra}", flush=True)


class CDPSession:
    def __init__(self, ws, sid=None):
        self.ws = ws
        self.sid = sid
        self._id = 0
        self._pending = {}
        self.events = []

    async def send(self, method, params=None):
        self._id += 1
        mid = self._id
        msg = {"id": mid, "method": method, "params": params or {}}
        if self.sid:
            msg["sessionId"] = self.sid
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self.ws.send(json.dumps(msg))
        return await asyncio.wait_for(fut, timeout=60)

    def _dispatch(self, data):
        if "id" in data and data["id"] in self._pending:
            self._pending.pop(data["id"]).set_result(data.get("result", data))
        elif "method" in data:
            self.events.append(data)


async def reader(ws, sessions):
    async for raw in ws:
        data = json.loads(raw)
        for s in list(sessions.values()):
            s._dispatch(data)


async def open_page(browser, ws, sessions, url, settle=2.5):
    t = await browser.send("Target.createTarget", {"url": "about:blank"})
    a = await browser.send(
        "Target.attachToTarget", {"targetId": t["targetId"], "flatten": True}
    )
    page = CDPSession(ws, a["sessionId"])
    sessions[a["sessionId"]] = page
    await page.send("Page.enable")
    await page.send("Runtime.enable")
    await page.send("Network.setCacheDisabled", {"cacheDisabled": True})
    await page.send("Page.navigate", {"url": url})
    for _ in range(80):
        if any(e["method"] == "Page.loadEventFired" for e in page.events):
            break
        await asyncio.sleep(0.2)
    await asyncio.sleep(settle)
    return page


async def js(page, expr):
    r = await page.send(
        "Runtime.evaluate",
        {"expression": expr, "returnByValue": True, "awaitPromise": True},
    )
    if "exceptionDetails" in r:
        return {"__exc": str(r["exceptionDetails"])}
    return r["result"].get("value")


def console_errors(page):
    out = []
    for e in page.events:
        if e["method"] == "Runtime.exceptionThrown":
            d = e["params"]["exceptionDetails"]
            out.append(d.get("exception", {}).get("description") or d.get("text"))
        if (
            e["method"] == "Runtime.consoleAPICalled"
            and e["params"]["type"] == "error"
        ):
            out.append(
                " ".join(
                    str(a.get("value", a.get("description", "")))
                    for a in e["params"]["args"]
                )
            )
    return out


async def main():
    ws = await websockets.connect(CDP, max_size=None)
    browser = CDPSession(ws)
    sessions = {"browser": browser}
    asyncio.create_task(reader(ws, sessions))

    # ══ A. UI 入口（index.html + app.js）══════════════════════════
    ed = await open_page(browser, ws, sessions, BASE + "/", settle=3.0)

    btn = await js(
        ed,
        """(() => {
      const b = document.getElementById("video-mode-btn");
      if (!b) return null;
      const r = b.getBoundingClientRect();
      return {
        w: Math.round(r.width), h: Math.round(r.height),
        text: b.textContent.trim(),
        hasSvg: !!b.querySelector("svg"),
        nextId: b.nextElementSibling && b.nextElementSibling.id,
        inTopbar: !!b.closest(".topbar-right"),
        // 疊層檢查：中心點最上層必須是這顆鈕（見 lessons-learned 2026-08-25）
        topAtCenter: (() => {
          const el = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
          return el && (el === b || b.contains(el));
        })(),
      };
    })()""",
    )
    check("A1 #video-mode-btn 存在且有量到尺寸", bool(btn) and btn["w"] > 0 and btn["h"] > 0,
          got=btn and (btn["w"], btn["h"]), want="w>0,h>0")
    check("A2 按鈕文字是「影片模式」", bool(btn) and "影片模式" in btn["text"],
          got=btn and btn["text"], want="含 影片模式")
    check("A3 icon 有注入 svg（data-icon=film 在 icons.js 找得到）", bool(btn) and btn["hasSvg"],
          got=btn and btn["hasSvg"], want=True)
    check("A4 位置：topbar-right 內、緊鄰 cam-btn 之前", bool(btn) and btn["inTopbar"] and btn["nextId"] == "cam-btn",
          got=btn and (btn["inTopbar"], btn["nextId"]), want=(True, "cam-btn"))
    check("A5 中心點最上層就是這顆鈕（沒被疊層蓋住）", bool(btn) and btn["topAtCenter"],
          got=btn and btn["topAtCenter"], want=True)

    # A6：真的點下去，攔 window.open 讀它被叫的參數
    opened = await js(
        ed,
        """(() => {
      const rec = [];
      const orig = window.open;
      window.open = (...a) => { rec.push(a); return null; };
      document.getElementById("video-mode-btn").click();
      window.open = orig;
      if (!rec.length) return null;
      const [url, target, feat] = rec[0];
      return { url, target, feat, abs: new URL(url, location.href).pathname, calls: rec.length };
    })()""",
    )
    check("A6 點擊會呼叫 window.open 一次", bool(opened) and opened["calls"] == 1,
          got=opened and opened["calls"], want=1)
    check("A7 開新分頁 + noopener", bool(opened) and opened["target"] == "_blank" and "noopener" in (opened["feat"] or ""),
          got=opened and (opened["target"], opened["feat"]), want=("_blank", "noopener"))
    check("A8 解析後路徑是 /static/video-edit-prototype.html", bool(opened) and opened["abs"] == "/static/video-edit-prototype.html",
          got=opened and opened["abs"], want="/static/video-edit-prototype.html")

    # A9 突變：修好前的相對路徑會解析成 /video-edit-prototype.html —— 實測它 404
    urls = await js(
        ed,
        """(async () => {
      const a = await fetch("/video-edit-prototype.html", {cache:"no-store"});
      const b = await fetch("/static/video-edit-prototype.html", {cache:"no-store"});
      return { relative: a.status, fixed: b.status };
    })()""",
    )
    check("A9 突變：舊的相對路徑 404、修好的 /static 路徑 200", urls == {"relative": 404, "fixed": 200},
          got=urls, want={"relative": 404, "fixed": 200})

    check("A10 編輯器頁無 console 錯誤", not console_errors(ed), got=console_errors(ed)[:2], want=[])

    # ══ B. 真模式載真字幕 ═════════════════════════════════════════
    real = await open_page(browser, ws, sessions, PROTO, settle=4.0)

    api = await js(real, """(async () => {
      const r = await fetch("/api/subtitles", {cache:"no-store"});
      const d = await r.json();
      const s = await fetch("sample-subtitles.json", {cache:"no-store"});
      const demo = await s.json();
      return { n: d.subs.length, last: d.subs[d.subs.length-1].text,
               demoN: demo.subs.length, demoLast: demo.subs[demo.subs.length-1].text };
    })()""")

    st = await js(real, """(() => {
      if (typeof window.__vt !== "object" || !window.__vt) return null;  // 先驗 hook 型別
      const s = window.__vt.subs || [];
      const note = document.getElementById("vt-empty-note");
      return {
        n: s.length,
        last: s.length ? s[s.length-1].text : null,
        lines: document.getElementById("vt-line-list").children.length,
        trackBlocks: document.getElementById("vt-sub-track").children.length,
        badge: document.getElementById("vt-mode-badge").textContent.trim(),
        noteHidden: note.hidden,
        noteText: note.textContent,
        duration: window.__vt.duration,
      };
    })()""")

    check("B1 徽章顯示「原型 · 真集」（不是 demo）", bool(st) and "真集" in st["badge"],
          got=st and st["badge"], want="原型 · 真集")
    check("B2 state.subs 句數 == /api/subtitles 句數", bool(st) and bool(api) and st["n"] == api["n"] and st["n"] > 0,
          got=st and (st["n"], api["n"]), want="相等且 >0")
    check("B3 最後一句文字 == API（且與 demo 假資料不同 → 證明讀的是真集）",
          bool(st) and bool(api) and st["last"] == api["last"] and api["last"] != api["demoLast"],
          got=st and (st["last"], api["demoLast"]), want="== API last, != demo last")
    check("B4 右欄逐句清單有渲染出 N 列", bool(st) and st["lines"] == api["n"],
          got=st and (st["lines"], api["n"]), want="相等")
    check("B5 時間軸字幕軌有塊", bool(st) and st["trackBlocks"] == api["n"],
          got=st and (st["trackBlocks"], api["n"]), want="相等")
    check("B6 失敗提示沒有出現「字幕載入失敗」", bool(st) and "字幕載入失敗" not in st["noteText"],
          got=st and st["noteText"][:60], want="不含 字幕載入失敗")

    # B7 突變：把 /api/subtitles 打掛，重載 → 字幕必須是 0 且提示要「看得見」（不准靜默）
    mut = await open_page(browser, ws, sessions, "about:blank", settle=0.2)
    await mut.send("Fetch.enable", {"patterns": [{"urlPattern": "*/api/subtitles*"}]})

    async def fail_subtitles():
        while True:
            for e in list(mut.events):
                if e["method"] == "Fetch.requestPaused":
                    mut.events.remove(e)
                    try:
                        await mut.send("Fetch.failRequest", {
                            "requestId": e["params"]["requestId"], "errorReason": "Failed"})
                    except Exception:
                        pass
            await asyncio.sleep(0.05)

    killer = asyncio.create_task(fail_subtitles())
    await mut.send("Page.navigate", {"url": PROTO})
    await asyncio.sleep(5.0)
    killer.cancel()
    m = await js(mut, """(() => {
      const note = document.getElementById("vt-empty-note");
      return { n: (window.__vt && window.__vt.subs || []).length,
               lines: document.getElementById("vt-line-list").children.length,
               noteHidden: note.hidden, noteText: note.textContent };
    })()""")
    check("B7 突變：字幕 API 打掛 → 句數退回 0（證明 B2 量到的真的來自 /api/subtitles）",
          bool(m) and m["n"] == 0 and m["lines"] == 0, got=m and (m["n"], m["lines"]), want=(0, 0))
    check("B8 突變：失敗有可見提示，不是靜默當成「這集沒字幕」",
          bool(m) and not m["noteHidden"] and "字幕載入失敗" in m["noteText"],
          got=m and (m["noteHidden"], m["noteText"][:40]), want="hidden=False 且含 字幕載入失敗")

    # ══ C. 確認框：真模式也要跳 ═══════════════════════════════════
    # 先解「沒選輸出目標 → 出片鈕 disabled」這道更前面的擋門，否則 click 不會有反應
    ready = await js(real, """(() => {
      const boxes = [...document.querySelectorAll("#vt-targets input[type=checkbox]")];
      if (!boxes.some(b => b.checked) && boxes.length) {
        boxes[0].checked = true;
        boxes[0].dispatchEvent(new Event("change", { bubbles: true }));
      }
      return { targets: boxes.length, checked: boxes.filter(b => b.checked).length,
               exportDisabled: document.getElementById("vt-export").disabled };
    })()""")
    check("C0 勾了輸出目標後出片鈕可按（沒選目標的擋門有效且可解）",
          bool(ready) and ready["checked"] >= 1 and not ready["exportDisabled"],
          got=ready, want="checked>=1, exportDisabled=False")

    c = await js(real, """(async () => {
      const seen = [];
      const posts = [];
      const oc = window.confirm, of = window.fetch;
      window.confirm = (m) => { seen.push(m); return false; };       // 先按取消
      window.fetch = (u, o) => { posts.push(String(u)); return of(u, o); };
      document.getElementById("vt-export").click();
      await new Promise(r => setTimeout(r, 600));
      const cancelled = { msg: seen[0] || null, calls: seen.length,
                          posted: posts.some(u => u.includes("/api/apply-plan")) };
      // 再按確定 —— 但攔住 POST 不讓它真的寫沙盒集
      seen.length = 0; posts.length = 0;
      window.confirm = () => true;
      window.fetch = (u, o) => {
        posts.push(String(u));
        if (String(u).includes("/api/apply-plan"))
          return Promise.resolve(new Response('{"ok":true}', {status:200, headers:{"Content-Type":"application/json"}}));
        return of(u, o);
      };
      document.getElementById("vt-export").click();
      await new Promise(r => setTimeout(r, 600));
      const confirmed = { calls: seen.length, posted: posts.some(u => u.includes("/api/apply-plan")) };
      window.confirm = oc; window.fetch = of;
      return { cancelled, confirmed };
    })()""")
    check("C1 真模式送出會跳確認框", bool(c) and c["cancelled"]["calls"] == 1,
          got=c and c["cancelled"]["calls"], want=1)
    check("C2 文案點名覆蓋 _v2.srt，且不誤稱示範資料",
          bool(c) and "_v2.srt" in (c["cancelled"]["msg"] or "") and "示範資料" not in (c["cancelled"]["msg"] or ""),
          got=c and c["cancelled"]["msg"], want="含 _v2.srt、不含 示範資料")
    check("C3 按取消 → 不送出（沒有打 /api/apply-plan）", bool(c) and not c["cancelled"]["posted"],
          got=c and c["cancelled"]["posted"], want=False)
    check("C4 突變：按確定 → 才真的打 /api/apply-plan（證明 C3 是確認框擋下的，不是別的原因）",
          bool(c) and c["confirmed"]["posted"], got=c and c["confirmed"]["posted"], want=True)

    # ══ D. demo 模式仍走自己的文案（證明 C2 是分支不是通用字串）══
    demo = await open_page(browser, ws, sessions, PROTO + "?demo=1", settle=4.0)
    d = await js(demo, """(async () => {
      const boxes = [...document.querySelectorAll("#vt-targets input[type=checkbox]")];
      if (!boxes.some(b => b.checked) && boxes.length) {
        boxes[0].checked = true;
        boxes[0].dispatchEvent(new Event("change", { bubbles: true }));
      }
      const seen = [];
      const oc = window.confirm; window.confirm = (m) => { seen.push(m); return false; };
      document.getElementById("vt-export").click();
      await new Promise(r => setTimeout(r, 400));
      window.confirm = oc;
      return { calls: seen.length, msg: seen[0] || null,
               badge: document.getElementById("vt-mode-badge").textContent.trim() };
    })()""")
    check("D1 demo 模式也跳確認框", bool(d) and d["calls"] == 1, got=d and d["calls"], want=1)
    check("D2 demo 文案另外點名「示範資料 / 假字幕」（分支有效）",
          bool(d) and "示範資料" in (d["msg"] or ""), got=d and d["msg"], want="含 示範資料")

    print("\n" + "=" * 60, flush=True)
    bad = [n for n, ok in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} 通過", flush=True)
    if bad:
        print("失敗：" + "、".join(bad), flush=True)
    sys.exit(1 if bad else 0)


asyncio.run(main())
