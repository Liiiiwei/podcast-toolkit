import asyncio, json, urllib.request, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page.enable","Runtime.enable","Network.enable"): await c.send(d)
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))}); await asyncio.sleep(3.5)
        for _ in range(30):
            st=await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState, v.seekable.length]})()")
            if st and st[0]>=1 and st[1]>0: break
            await asyncio.sleep(0.4)
        # 初始態：完全沒碰過播放頭
        txt=await c.ev("document.getElementById('vt-time').textContent")
        dur=(await c.ev("__vtStats()"))["duration"]
        print(f"【X0】初始態播放列：'{txt}'　state.duration={dur}")
        print(f"     總長不是 00:00.0：", txt.split('/')[-1].strip()!="00:00.0",
              "　等於原長：", txt.split('/')[-1].strip()==f"00:{dur:04.1f}")
        # 突變測試：把 duration 打回 0 再設回來，確認這行真的在做事
        await c.ev("document.getElementById('vt-time').textContent='XX'")
        await c.ev("(()=>{__vt.duration=0;})()")
        await c.ev("(()=>{const v=document.getElementById('vt-video');v.dispatchEvent(new Event('loadedmetadata'));})()")
        await asyncio.sleep(0.5)
        print(f"【X1】重新觸發 loadedmetadata → '{await c.ev('document.getElementById(\"vt-time\").textContent')}'（應回填而非停在 XX）")
        print("例外：", len(c.exc))
        for e in c.exc: print("  ", e[:300])
asyncio.run(main())
