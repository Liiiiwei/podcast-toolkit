import asyncio, json, urllib.request, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable"); await c.send("Network.enable")
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&n="+str(id(c))})
        for _ in range(30):
            if await c.ev("(typeof __vtSubs==='function')?__vtSubs().length:0"): break
            await asyncio.sleep(0.4)
        print("video 狀態：", await c.ev("""(()=>{const v=document.getElementById('vt-video');
          return {src:v.currentSrc||v.getAttribute('src'), ready:v.readyState, net:v.networkState,
                  dur:isFinite(v.duration)?v.duration:String(v.duration), err:v.error?v.error.code:null}})()"""))
        print("設 currentTime=13.45 →", await c.ev("(()=>{const v=document.getElementById('vt-video');v.currentTime=13.45;return v.currentTime})()"))
        print("state.duration：", await c.ev("__vtStats().duration"))
        print("__vtSeek(13.45) →", await c.ev("__vtSeek(13.45)"))
        print("播放頭 DOM left：", await c.ev("document.getElementById('vt-playhead')?.style.left"))
asyncio.run(main())
