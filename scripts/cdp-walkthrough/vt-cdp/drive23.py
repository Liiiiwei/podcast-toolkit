import asyncio, json, urllib.request, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable"); await c.send("Network.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))}); await asyncio.sleep(3.0)
        for _ in range(30):
            r=await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState,v.seekable.length]})()")
            if r and r[0]>=1 and r[1]>0: break
            await asyncio.sleep(0.3)
        async def st(): return await c.ev("""(()=>{const e=document.querySelector('#vt-card-track .vt-card');
          const r=e.getBoundingClientRect();return {w:Math.round(r.width),h:e.querySelectorAll('.vt-card-h').length,
          y:r.y+r.height/2,left:r.x,right:r.x+r.width}})()""")
        pxps=await c.ev("(()=>{const r=document.getElementById('vt-card-track').getBoundingClientRect();return r.width/__vtStats().duration})()")
        print("== U10 窄卡的出路：放大時間軸 → 把手回來 → 拉得長 ==")
        await c.ev("__vtPromote(4)"); await asyncio.sleep(0.4)
        b=await st(); await c.drag(b["right"]-3,b["y"],b["left"]-pxps*5,b["y"],steps=14); await asyncio.sleep(0.4)
        print("  縮到最短：", await st())
        zoom=await c.ev("(()=>{const b=document.getElementById('vt-zoom-in');if(!b)return null;const r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
        print("  找到放大鈕：", bool(zoom))
        for i in range(5):
            await c.click(zoom["x"],zoom["y"]); await asyncio.sleep(0.4)
            s=await st(); print(f"   放大第{i+1}次 → 塊寬 {s['w']}px 把手 {s['h']}")
            if s["h"]==2: break
        s=await st()
        if s["h"]==2:
            pxps2=await c.ev("(()=>{const r=document.getElementById('vt-card-track').getBoundingClientRect();return r.width/__vtStats().duration})()")
            before=await c.ev("__vtCards().map(c=>+(c.end-c.start).toFixed(3))")
            await c.drag(s["right"]-3,s["y"],s["right"]-3+pxps2*1.0,s["y"],steps=12); await asyncio.sleep(0.4)
            after=await c.ev("__vtCards().map(c=>+(c.end-c.start).toFixed(3))")
            print("  放大後拉長：長度", before, "→", after, " 真的拉得長：", after[0]-before[0]>0.8)
        else:
            print("  ✗ 放大後把手沒回來 —— 窄卡會被卡死")
        await c.shot("U10-放大後把手")
        print("== console exception：", len(c.exc))
        for e in c.exc: print("  ",e[:300])
asyncio.run(main())
