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
        for _ in range(60):
            r=await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState,v.seekable.length]})()")
            if r and r[0]>=1 and r[1]>0: break
            await asyncio.sleep(0.5)
        await c.ev("__vtPromote(3)"); await asyncio.sleep(0.6)
        info=await c.ev("""(()=>{const blk=document.querySelector('#vt-card-track .vt-card');
          const b=document.querySelector('#vt-card-track .vt-card-del');
          const br=b.getBoundingClientRect(), kr=blk.getBoundingClientRect();
          const cx=br.x+br.width/2, cy=br.y+br.height/2;
          const top=document.elementFromPoint(cx,cy);
          const handles=Array.from(blk.querySelectorAll('[class*=handle],[class*=grip],[data-mode]')).map(h=>h.className);
          return {卡塊寬:Math.round(kr.width), 刪鈕:[Math.round(br.x),Math.round(br.y),Math.round(br.width),Math.round(br.height)],
                  該點最上層:top?top.className||top.tagName:null, 把手:handles,
                  子元素:Array.from(blk.children).map(x=>x.className)}})()""")
    print(json.dumps(info,ensure_ascii=False,indent=1))
asyncio.run(main())
