import asyncio, json, urllib.request, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
async def main():
    pages=[t for t in hj("/json/list") if t["type"]=="page"]
    page=next((t for t in pages if "video-edit-prototype" in t["url"]), pages[-1])
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for m in ("Page.enable","Runtime.enable","Network.enable"): await c.send(m)
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&z=1"})
        for _ in range(30):
            await asyncio.sleep(0.4)
            if await c.ev("(typeof __vtSubs==='function')?__vtSubs().length:0"): break
        c.exc.clear()
        print("video:", await c.ev("(()=>{const v=document.getElementById('vt-video');return{src:!!v.currentSrc,ready:v.readyState,dur:v.duration,paused:v.paused}})()"))
        await c.ev("""(()=>{window.__ev=[];const v=document.getElementById('vt-video');
          ['seeked','timeupdate','seeking','loadedmetadata'].forEach(k=>v.addEventListener(k,()=>window.__ev.push(k)));})()""")
        subs=await c.ev("__vtSubs()")
        mid=(subs[9]["start"]+subs[9]["end"])/2
        await c.ev(f"(()=>{{document.getElementById('vt-video').currentTime={mid};}})()")
        await asyncio.sleep(1.5)
        print("設 currentTime=%.2f 後事件："%mid, await c.ev("window.__ev"))
        print("currentTime 讀回：", await c.ev("document.getElementById('vt-video').currentTime"))
        print("is-playing 數：", await c.ev("document.querySelectorAll('.vt-line.is-playing').length"))
        # 直接手動呼叫 render 路徑看邏輯本身對不對
        print("手動觸發 seeked 後：", await c.ev("""(()=>{document.getElementById('vt-video').dispatchEvent(new Event('seeked'));
          return Array.from(document.querySelectorAll('.vt-line.is-playing')).map(n=>+n.dataset.i)})()"""))
        print("例外：",len(c.exc)); [print(" ",e[:300]) for e in c.exc]
asyncio.run(main())
