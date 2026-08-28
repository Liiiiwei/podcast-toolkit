import asyncio, json, urllib.request, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable"); await c.send("Network.enable")
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL+"&n="+str(id(c))})
        for _ in range(30):
            if await c.ev("(typeof __vtSubs==='function')?__vtSubs().length:0"): break
            await asyncio.sleep(0.4)
        # 等影片可 seek 再操作：載入中設 currentTime 不會生效
        for _ in range(30):
            if await c.ev("(()=>{const v=document.getElementById('vt-video');return v.readyState>=1&&v.seekable.length>0})()"): break
            await asyncio.sleep(0.4)
        print("video readyState/seekable：", await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState,v.seekable.length]})()"))
        # 第5句升格成標題卡 → 選取（播放頭應自動落進卡區間，疊層看得到）
        card=await c.ev("__vtPromote(4)")
        await asyncio.sleep(0.6)
        print("卡：", {k:card[k] for k in ('tpl','text','start','end')} if card else None)
        print("播放頭：", await c.ev("document.getElementById('vt-video').currentTime"))
        print("疊層可見：", await c.ev("!!document.querySelector('#vt-card-layer .vt-card-view')"))
        print("面板 hidden：", await c.ev("document.getElementById('vt-card-inspector').hidden"))
        await c.shot("J1-標題卡選取")
asyncio.run(main())
