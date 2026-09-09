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
            r=await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState, v.seekable.length]})()")
            if r and r[0]>=1 and r[1]>0: break
            await asyncio.sleep(0.3)
        async def blk():
            return await c.ev("""(()=>{const e=document.querySelector('#vt-card-track .vt-card');const r=e.getBoundingClientRect();
              return {y:r.y+r.height/2,w:r.width,cx:r.x+r.width/2,left:r.x,right:r.x+r.width}})()""")
        async def cards():
            return await c.ev("__vtCards().map(c=>({s:+c.start.toFixed(3),e:+c.end.toFixed(3),d:+(c.end-c.start).toFixed(3)}))")
        async def handles():
            return await c.ev("document.querySelectorAll('#vt-card-track .vt-card .vt-card-h').length")
        pxps=await c.ev("(()=>{const r=document.getElementById('vt-card-track').getBoundingClientRect();return r.width/__vtStats().duration})()")

        print("== U9 窄卡不畫把手，但仍拖得動 ==")
        await c.ev("__vtPromote(4)"); await asyncio.sleep(0.4)  # 較長的一句
        print("  升格後：", await cards(), " 塊寬 %.0fpx"%(await blk())["w"], " 把手數：", await handles())
        b=await blk()
        await c.drag(b["right"]-3,b["y"],b["left"]-pxps*5,b["y"],steps=14); await asyncio.sleep(0.4)  # 縮到最短
        nb=await blk()
        print("  縮到最短：", await cards(), " 塊寬 %.0fpx"%nb["w"], " 把手數：", await handles(), "（<26px 應為 0）")
        before=(await cards())[0]
        await c.drag(nb["cx"],nb["y"],nb["cx"]-pxps*4.0,nb["y"],steps=12); await asyncio.sleep(0.4)
        after=(await cards())[0]
        print("  窄卡整塊拖 −4 秒：", before, "→", after, " 拖得動：", abs((before['s']-after['s'])-4.0)<0.15, " 長度不變：", abs(after['d']-before['d'])<1e-3)
        b=await blk()
        await c.drag(b["cx"],b["y"],b["cx"]+pxps*6.0,b["y"],steps=12); await asyncio.sleep(0.4)
        wide=await c.ev("""(()=>{const e=document.querySelector('#vt-card-track .vt-card');
          return {w:Math.round(e.getBoundingClientRect().width), h:e.querySelectorAll('.vt-card-h').length}})()""")
        print("  拖長回來後：", wide, "（>=26px 把手應回來 2 個）— 需先拉長，這裡確認未回來時 h=0：", wide)
        await c.shot("U9-窄卡")
        print("== console exception：", len(c.exc))
        for e in c.exc: print("  ",e[:300])
asyncio.run(main())
