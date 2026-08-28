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
        await c.send("Page.navigate",{"url":URL+"&n=p18"}); await asyncio.sleep(1.0)
        for _ in range(25):
            if await c.ev("(typeof __vtSubs==='function')&&__vtSubs()?1:0"): break
            await asyncio.sleep(0.4)
        c.exc.clear()

        print("【問題 A】⌘Z 能不能復原斷句？")
        await c.ev("__vtAddCut(8.0,9.0)"); await asyncio.sleep(0.3)
        print("  先做一段剪除，cuts=", await c.ev("__vtStats().cutTotal"))
        await c.ev("__vtStartEdit(2)"); await asyncio.sleep(0.3)
        await c.key("Home",36)
        for _ in range(6): await c.key("ArrowRight",39)
        await c.key("Enter",13); await asyncio.sleep(0.5)
        print("  斷句後句數=", await c.ev("__vtSubs().length"))
        await c.key("Escape",27); await asyncio.sleep(0.2)
        await c.ev("document.body.focus()"); await asyncio.sleep(0.2)
        await c.send("Input.dispatchKeyEvent",{"type":"rawKeyDown","key":"z","code":"KeyZ","windowsVirtualKeyCode":90,"modifiers":4})
        await c.send("Input.dispatchKeyEvent",{"type":"keyUp","key":"z","code":"KeyZ","windowsVirtualKeyCode":90,"modifiers":4})
        await asyncio.sleep(0.5)
        print("  按 ⌘Z 後 → 句數=", await c.ev("__vtSubs().length"), " 剪除總長=", await c.ev("__vtStats().cutTotal"))
        print("  ⇒ 期待復原斷句，實際復原的是剪除？")

        print("\n【問題 E】播放時右欄有沒有高亮目前句／自動捲")
        print("  is-playing 之類的 class：", await c.ev("document.querySelectorAll('#vt-line-list .vt-line.is-playing, #vt-line-list .vt-line.is-now').length"))
        await c.ev("(()=>{const v=document.getElementById('vt-video');v.currentTime=17.5;})()"); await asyncio.sleep(0.8)
        print("  跳到 17.5s 後，右欄有標示第幾句嗎：",
              await c.ev("[...document.querySelectorAll('#vt-line-list .vt-line')].map((n,i)=>n.className).filter(x=>x!=='vt-line')"))

        print("\n【問題 D】點時間軸字幕塊會不會 seek")
        await c.ev("(()=>{const v=document.getElementById('vt-video');v.currentTime=0;})()"); await asyncio.sleep(0.4)
        r=await c.ev("(()=>{const s=document.querySelectorAll('#vt-sub-track .vt-sub')[6];const b=s.getBoundingClientRect();return{x:b.x+b.width/2,y:b.y+b.height/2}})()")
        await c.click(r["x"],r["y"]); await asyncio.sleep(0.5)
        print("  點第7塊後 currentTime=", await c.ev("document.getElementById('vt-video').currentTime"), "（0 = 沒 seek）")

        print("\n【問題 P】選取的標題卡會不會在不該出現的時間點硬顯示")
        await c.ev("__vtPromote(1)"); await asyncio.sleep(0.4)
        cd=(await c.ev("__vtCards()"))[0]
        print(f"  卡區間 {cd['start']:.2f}–{cd['end']:.2f}")
        await c.ev("(()=>{const v=document.getElementById('vt-video');v.currentTime=20.0;})()"); await asyncio.sleep(0.9)
        print("  播放頭移到 20.0s（區間外），疊層還在嗎：", await c.ev("!!document.querySelector('#vt-card-layer .vt-card-view')"))

        print("\n【問題 J】同一句重複按「卡」")
        await c.ev("__vtPromote(1)"); await c.ev("__vtPromote(1)"); await asyncio.sleep(0.4)
        cards=await c.ev("__vtCards()")
        print("  對第2句連按 3 次 → 卡數=", len(cards), [f"{x['start']:.2f}-{x['end']:.2f}" for x in cards])

        print("\n【問題 Q】沒選卡時點版型縮圖")
        await c.ev("(()=>{document.getElementById('vt-card-track').click();})()"); await asyncio.sleep(0.3)
        print("  取消選取後 selectedCard=", await c.ev("(()=>{const i=document.getElementById('vt-card-inspector');return i.hidden?'無選取':'仍有選取'})()"))
        n0=len(await c.ev("__vtCards()"))
        r=await c.ev("(()=>{const t=document.querySelector('#vt-tpl-grid .vt-tpl[data-tpl=\"quote\"]').getBoundingClientRect();return{x:t.x+t.width/2,y:t.y+t.height/2}})()")
        await c.click(r["x"],r["y"]); await asyncio.sleep(0.4)
        print(f"  點「引言框」縮圖 → 卡數 {n0} → {len(await c.ev('__vtCards()'))}（沒變＝點了沒反應）")
        print("\n例外：",len(c.exc)); [print("  ",e[:200]) for e in c.exc]
asyncio.run(main())
