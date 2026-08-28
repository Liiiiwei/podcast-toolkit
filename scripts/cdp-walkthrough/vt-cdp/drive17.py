import asyncio, json, urllib.request, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
async def main():
    pages=[t for t in hj("/json/list") if t["type"]=="page"]
    page=next((t for t in pages if "video-edit-prototype" in t["url"]), pages[-1])
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable"); await c.send("Network.enable")
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL+"&n=c"}); await asyncio.sleep(3.0); c.exc.clear()
        subs=None
        for _ in range(25):
            subs=await c.ev("(typeof __vtSubs==='function')?__vtSubs():null")
            if subs: break
            await asyncio.sleep(0.4)
        s=subs[5]; print("整段包住第6句：",s["text"],round(s["start"],2),round(s["end"],2))
        await c.ev(f"__vtAddCut({s['start']-0.1},{s['end']+0.1})"); await asyncio.sleep(0.5)
        ls=await c.ev("__vtLines()")
        print("淡化列：",[l["text"][:12] for l in ls if l["cut"]])
        st=await c.ev("(()=>{const el=document.querySelector('#vt-line-list .vt-line.is-cut');if(!el)return null;const inp=el.querySelector('.vt-line-input');return{opacity:getComputedStyle(el).opacity,deco:getComputedStyle(inp).textDecorationLine}})()")
        print("剪除列樣式：",st)
        # 標題卡 + 播放頭停在卡上，做總覽圖
        await c.ev("__vtPromote(1)")
        await c.ev("__vtStartEdit(4)"); await asyncio.sleep(0.4)
        r=await c.ev("(()=>{const row=document.querySelector('#vt-line-list .vt-line:focus-within').getBoundingClientRect();return{x:row.x+row.width*0.5,y:row.y+row.height/2}})()")
        await c.send("Input.dispatchMouseEvent",{"type":"mouseMoved","x":r["x"],"y":r["y"]})
        await c.ev("(()=>{const v=document.getElementById('vt-video');v.currentTime=3.0;})()"); await asyncio.sleep(1.0)
        await c.shot("H5-簡化後")
        print("例外：",len(c.exc))
asyncio.run(main())
