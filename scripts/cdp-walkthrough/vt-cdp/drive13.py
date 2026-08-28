import asyncio, json, urllib.request, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL}); await asyncio.sleep(3.5)
        print("== J 標題卡只在自己的時間區間出現 ==")
        cd=await c.ev("__vtPromote(2)")
        print(f"  卡片區間 {cd['start']:.2f}–{cd['end']:.2f}")
        # 取消選取（點軌道空白處）
        r=await c.ev("(()=>{const k=document.getElementById('vt-card-track').getBoundingClientRect();return {x:k.x+k.width*0.9,y:k.y+k.height/2}})()")
        await c.click(r["x"],r["y"]); await asyncio.sleep(0.3)
        print("  取消選取後 selectedCard：", await c.ev("__vtCards().length && document.querySelectorAll('#vt-card-track .vt-card.is-selected').length"))
        for t in [0.5, 5.0, 10.0]:
            await c.ev(f"(()=>{{const v=document.getElementById('vt-video');v.currentTime={t};}})()")
            await asyncio.sleep(0.5)
            await c.ev("__vtSelectCard(null)")
            v=await c.ev("(()=>{const e=document.querySelector('#vt-card-layer .vt-card-view');return e?e.textContent:null})()")
            print(f"  currentTime={t:>4}s → 疊層：{v if v else '（無，正確）'}")
        print("== K 多張卡並存 ==")
        await c.ev("__vtDropTpl('lower', 12.0)")
        await c.ev("__vtDropTpl('quote', 18.0)")
        await asyncio.sleep(0.4)
        print("  軌道塊：")
        for b in await c.ev("__vtCardTrack()"): print("   ", b)
        await c.ev("__vtSelectCard(null)")
        await c.ev("(()=>{const v=document.getElementById('vt-video');v.currentTime=13.0;})()")
        await asyncio.sleep(0.6)
        print("  13s 時疊層：", await c.ev("__vtCardView() && __vtCardView().cls"))
        await c.shot("E-三張卡並存")
        print("== console exception：", len(c.exc))
        for e in c.exc: print("   ",e[:300])
asyncio.run(main())
