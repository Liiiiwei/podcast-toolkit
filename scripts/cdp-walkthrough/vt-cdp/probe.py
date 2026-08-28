import asyncio, json
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        print("網址：", await c.ev("location.href"))
        print("hook：", await c.ev("Object.keys(window).filter(k=>k.startsWith('__vt'))"))
        print("載入的 js 尾段有沒有新 hook：", await c.ev(
          "fetch(location.origin+'/static/video-edit-prototype.js').then(r=>r.text()).then(t=>t.includes('__vtPlanPush'))"))
        print("例外：", c.exc[:2])
asyncio.run(main())
