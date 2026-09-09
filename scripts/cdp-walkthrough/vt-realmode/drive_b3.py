#!/usr/bin/env python3
# B3 補測：沙盒集的 _v2.srt 內容剛好與 demo 假資料一字不差（11 句全同），
# 所以「文字不同」證明不了讀的是真集。改用真正的來源證明：
# 動 _final_v2.srt 這個檔 → 真模式頁面跟著變；還原 → 跟著變回來。
# demo 頁面全程不受影響（它讀的是 static 假資料）。
import asyncio, json, shutil, sys
from pathlib import Path

import websockets

results = []


def check(name, ok, got=None, want=None):
    results.append((name, bool(ok)))
    extra = "" if got is None else f"  got={got!r} want={want!r}"
    print(f"{'✓' if ok else '✗'} {name}{extra}", flush=True)


class CDPSession:
    def __init__(self, ws, sid=None):
        self.ws, self.sid = ws, sid
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


CDP = sys.argv[1]
BASE = "http://127.0.0.1:8792"
PROTO = f"{BASE}/static/video-edit-prototype.html"
SRT = Path("/private/tmp/pt-e2e/20260825 端對端測試/03_成品/端對端測試_final_v2.srt")
TMP = SRT.with_suffix(".srt.b3tmp")
MARK = "B3走查專用文字請勿保留"


async def main():
    ws = await websockets.connect(CDP, max_size=None)
    browser = CDPSession(ws)
    sessions = {"browser": browser}
    asyncio.create_task(reader(ws, sessions))

    orig = SRT.read_text(encoding="utf-8")
    shutil.copy2(SRT, TMP)  # 原檔備份，走查結束一定還原
    try:
        SRT.write_text(orig.replace("是不是很方便呢", MARK), encoding="utf-8")

        p = await open_page(browser, ws, sessions, PROTO, settle=4.0)
        got = await js(p, """(() => {
          const s = window.__vt.subs || [];
          return { n: s.length, last: s.length ? s[s.length-1].text : null };
        })()""")
        check("B3a 改了這集的 _final_v2.srt → 真模式頁面跟著變（證明讀的是這個檔）",
              bool(got) and got["last"] == MARK, got=got and got["last"], want=MARK)

        d = await open_page(browser, ws, sessions, PROTO + "?demo=1", settle=4.0)
        dg = await js(d, """(() => {
          const s = window.__vt.subs || [];
          return { last: s.length ? s[s.length-1].text : null };
        })()""")
        check("B3b 同時 demo 模式不受影響（兩個來源真的是分開的）",
              bool(dg) and dg["last"] == "是不是很方便呢", got=dg and dg["last"], want="是不是很方便呢")
    finally:
        shutil.copy2(TMP, SRT)
        TMP.unlink()

    check("B3c 還原後檔案內容與原本一字不差", SRT.read_text(encoding="utf-8") == orig,
          got="相同" if SRT.read_text(encoding="utf-8") == orig else "不同", want="相同")

    p2 = await open_page(browser, ws, sessions, PROTO, settle=4.0)
    g2 = await js(p2, """(() => {
      const s = window.__vt.subs || [];
      return { n: s.length, last: s.length ? s[s.length-1].text : null };
    })()""")
    check("B3d 還原後頁面也退回原文字（前後對照閉環）",
          bool(g2) and g2["n"] == 11 and g2["last"] == "是不是很方便呢",
          got=g2, want="11 句、最後一句 是不是很方便呢")

    print("\n" + "=" * 60, flush=True)
    bad = [n for n, ok in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} 通過", flush=True)
    if bad:
        print("失敗：" + "、".join(bad), flush=True)
    sys.exit(1 if bad else 0)


asyncio.run(main())
