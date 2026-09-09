import asyncio, json
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
URL="http://127.0.0.1:8877/static/video-edit-prototype.html?v=probe"
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Page.navigate",{"url":URL})
        for _ in range(60):
            if await c.ev("typeof window.__vtDropTpl")=="function": break
            await asyncio.sleep(0.5)
        for _ in range(40):
            if await c.ev("__vt.duration>0"): break
            await asyncio.sleep(0.25)
        for t in (2.0, 11.0, 13.0): await c.ev("__vtDropTpl('big', %s)"%t)
        await c.ev("__vtAddCut(10.0, 14.0)")
        Q="[...document.querySelectorAll('#vt-card-track .vt-card')]"
        print("卡數：", await c.ev(Q+".length"))
        print("class：", await c.ev(Q+".map(e=>e.className)"))
        print("有 txt：", await c.ev(Q+".map(e=>!!e.querySelector('.vt-card-txt'))"))
        print("子層 class：", await c.ev(Q+".map(e=>[...e.children].map(x=>x.className))"))
        print("title：", json.dumps(await c.ev(Q+".map(e=>e.title)"), ensure_ascii=False))
        print("樣式：", await c.ev(Q+".map(e=>{const s=getComputedStyle(e);return[s.opacity,s.borderTopStyle]})"))
asyncio.run(main())
