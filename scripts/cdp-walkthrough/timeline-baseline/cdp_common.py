#!/usr/bin/env python3
# 共用 CDP 驅動：抄自 scripts/cdp-walkthrough/vt-realmode/drive_entry.py 的
# CDPSession/open_page/js/check/console_errors（README:79-88 斷言紀律同源），
# 額外加「真事件」滑鼠序列 helper（down→多次 move→up）與 elementFromPoint 疊層驗證。
import asyncio
import json

import websockets

# 每筆 (name, ok, got, want, status)；status ∈ {"pass","fail","skip"}。
# skip 不計入通過/失敗分母（#4：避免「恆真的略過」被灌水成 PASS）。
results = []


def check(name, ok, got=None, want=None):
    ok = bool(ok)
    results.append((name, ok, got, want, "pass" if ok else "fail"))
    mark = "PASS" if ok else "FAIL"
    extra = "" if got is None else f"  got={got!r} want={want!r}"
    print(f"[{mark}] {name}{extra}", flush=True)
    return ok


def skip(name, reason=""):
    """明確略過：不計入 PASS，也不算 FAIL。用於前提不成立、無法有意義斷言時。
    禁止用 check(name, True, ...) 假裝通過（那是恆真的裝飾，等於沒測）。"""
    results.append((name, None, reason, None, "skip"))
    print(f"[SKIP] {name}  ({reason})", flush=True)
    return None


def summary():
    bad = [n for n, ok, _, _, st in results if st == "fail"]
    passed = [n for n, ok, _, _, st in results if st == "pass"]
    skipped = [n for n, ok, _, _, st in results if st == "skip"]
    print("\n" + "=" * 60, flush=True)
    tail = f"（另有 {len(skipped)} 項略過，不計入分母）" if skipped else ""
    print(f"{len(passed)}/{len(passed) + len(bad)} 通過{tail}", flush=True)
    if bad:
        print("失敗：" + "、".join(bad), flush=True)
    return bad


def results_as_dict():
    return [
        {"name": n, "ok": ok, "got": got, "want": want, "status": st}
        for n, ok, got, want, st in results
    ]


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


async def connect(cdp_ws_url):
    """連上 browser-level ws，回傳 (browser_session, sessions dict)。"""
    ws = await websockets.connect(cdp_ws_url, max_size=None)
    browser = CDPSession(ws)
    sessions = {"browser": browser}
    asyncio.create_task(reader(ws, sessions))
    return browser, sessions, ws


async def open_page(browser, ws, sessions, url, settle=2.5, width=1400, height=1600):
    # 預設視窗故意開夠高（1600）：時間軸卡片列表在真集數會超過預設 headless 視窗高度
    # （實測預設 innerHeight=469，時間軸區塊落在 y≈473，直接落在可視區之外），
    # 沒設 viewport 時 elementFromPoint(cx,cy) 對可視區外的座標一律回 null，
    # 會被誤判成「疊層吃掉點擊」。用 Emulation.setDeviceMetricsOverride 撐大視窗
    # 讓整條時間軸落在可視區內，才能做到硬要求的 elementFromPoint 驗證。
    t = await browser.send("Target.createTarget", {"url": "about:blank"})
    a = await browser.send(
        "Target.attachToTarget", {"targetId": t["targetId"], "flatten": True}
    )
    page = CDPSession(ws, a["sessionId"])
    sessions[a["sessionId"]] = page
    await page.send("Page.enable")
    await page.send("Runtime.enable")
    await page.send("Network.setCacheDisabled", {"cacheDisabled": True})
    await page.send(
        "Emulation.setDeviceMetricsOverride",
        {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
    )
    await page.send("Page.navigate", {"url": url})
    for _ in range(100):
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


async def mouse_click(page, x, y):
    """真事件單擊：down→up 同一點，不帶 move（用於已知安全落點的簡單點擊）。"""
    await page.send(
        "Input.dispatchMouseEvent",
        {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
    )
    await page.send(
        "Input.dispatchMouseEvent",
        {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
    )


async def mouse_drag(page, x0, y0, x1, y1, steps=8, settle=0.03):
    """真事件拖曳：完整序列 down → 多次 move → up（硬要求：不可只送 down/up）。"""
    await page.send(
        "Input.dispatchMouseEvent",
        {"type": "mousePressed", "x": x0, "y": y0, "button": "left", "clickCount": 1},
    )
    await asyncio.sleep(settle)
    for i in range(1, steps + 1):
        x = x0 + (x1 - x0) * i / steps
        y = y0 + (y1 - y0) * i / steps
        await page.send(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": x, "y": y, "button": "left"},
        )
        await asyncio.sleep(settle)
    await page.send(
        "Input.dispatchMouseEvent",
        {"type": "mouseReleased", "x": x1, "y": y1, "button": "left", "clickCount": 1},
    )
    await asyncio.sleep(settle)


async def element_rect_center(page, selector):
    """回傳 {x,y,w,h,cx,cy}；元素不存在回 None。"""
    return await js(
        page,
        f"""(() => {{
      const el = document.querySelector({json.dumps(selector)});
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {{ x: r.left, y: r.top, w: r.width, h: r.height,
                cx: r.left + r.width/2, cy: r.top + r.height/2 }};
    }})()""",
    )


async def top_element_at(page, x, y):
    """document.elementFromPoint 回傳的元素描述（tag+id+class），驗疊層真的沒吃掉點擊。"""
    return await js(
        page,
        f"""(() => {{
      const el = document.elementFromPoint({x}, {y});
      if (!el) return null;
      return {{ tag: el.tagName, id: el.id || null, cls: el.className || null }};
    }})()""",
    )
