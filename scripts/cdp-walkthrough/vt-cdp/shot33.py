import asyncio, json
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))}); await asyncio.sleep(3.5)
        for _ in range(40):
            r=await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState,v.seekable.length]})()")
            if r and r[0]>=1 and r[1]>0: break
            await asyncio.sleep(0.4)
        await c.ev("__vtPromote(3)"); await asyncio.sleep(0.5)
        await c.ev("__vtSeek((__vt.cards[0].start+__vt.cards[0].end)/2)"); await asyncio.sleep(0.6)
        await c.shot("N-四項完成-展開態")
        # 收起標題卡區塊
        pt=await c.ev("""(()=>{const h=document.querySelector('.vt-side-sec[data-sec="card"] > .vt-side-head');
          const r=h.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()""")
        await c.click(pt["x"],pt["y"]); await asyncio.sleep(0.5)
        await c.shot("N-標題卡區塊收起-字幕清單長高")
        print("shots:", SHOTS)
asyncio.run(main())
