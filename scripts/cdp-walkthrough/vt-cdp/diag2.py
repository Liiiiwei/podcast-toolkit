import asyncio, json
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime"): await c.send(d+".enable")
        print("samp 長度：", await c.ev("window.__samp?window.__samp.length:'undefined'"))
        print("__vtCards：", await c.ev("typeof window.__vtCards"))
        print("__vtSubs：", await c.ev("typeof window.__vtSubs"))
        r=await c.send("Runtime.evaluate",{"expression":"(()=>{const subs=__vt().subs;return subs.length})()","returnByValue":True})
        print("subs.length 原始回應：", json.dumps(r.get("result",{}))[:200], "例外：", json.dumps(r.get("exceptionDetails",{}))[:300])
        r=await c.send("Runtime.evaluate",{"expression":"(()=>{const c=__vtCards();return c.length})()","returnByValue":True})
        print("cards.length：", json.dumps(r.get("result",{}))[:200], "例外：", json.dumps(r.get("exceptionDetails",{}))[:300])
        r=await c.send("Runtime.evaluate",{"expression":"""(()=>{const subs=__vt().subs;
          const bad=[];for(const [t,vis,txt,nc] of window.__samp){const s=subs.find(x=>t>=x.start&&t<x.end)||null;}
          return 'ok'})()""","returnByValue":True})
        print("迴圈測試：", json.dumps(r.get("result",{}))[:200], "例外：", json.dumps(r.get("exceptionDetails",{}))[:400])
asyncio.run(main())
