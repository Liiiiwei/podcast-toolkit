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
        # 先 navigate 一次把舊版沖掉，清掉舊頁殘留的例外，再 navigate 一次才開始計數
        await c.send("Page.navigate",{"url":URL+"&n=a"}); await asyncio.sleep(2.5)
        c.exc.clear()
        await c.send("Page.navigate",{"url":URL+"&n=b"}); await asyncio.sleep(3.0)
        print("載入後例外數：",len(c.exc));  [print("  ",e[:300]) for e in c.exc]
        await c.shot("H1-初始全覽")

        # 焦點在某一列 → 工具鈕浮出（截圖看得到）
        await c.ev("__vtStartEdit(4)"); await asyncio.sleep(0.5)
        r=await c.ev("(()=>{const row=document.querySelector('#vt-line-list .vt-line:focus-within');const b=row.getBoundingClientRect();return{x:b.x+b.width*0.5,y:b.y+b.height/2}})()")
        await c.send("Input.dispatchMouseEvent",{"type":"mouseMoved","x":r["x"],"y":r["y"]}); await asyncio.sleep(0.4)
        print("焦點列：", await c.ev("__vtEditBox()"))
        await c.shot("H2-編輯中")

        # 加一張標題卡＋兩段剪除，看右欄狀態語彙
        await c.ev("__vtPromote(1)")
        await c.ev("__vtAddCut(8.0,10.0)")
        await c.ev("__vtAddCut(14.5,17.0)")
        await asyncio.sleep(0.6)
        ls=await c.ev("__vtLines()")
        print("整段剪除（淡化）：",[l["text"][:10] for l in ls if l["cut"]])
        print("部分剪除：",[l["text"][:10] for l in ls if l["partial"]])
        v=await c.ev("(()=>{const el=document.querySelector('#vt-line-list .vt-line.is-cut');if(!el)return null;const s=getComputedStyle(el);return{opacity:s.opacity,deco:getComputedStyle(el.querySelector('.vt-line-input')).textDecorationLine}})()")
        print("剪除列樣式：",v)
        await c.ev("(()=>{const vd=document.getElementById('vt-video');vd.currentTime=2.5;})()"); await asyncio.sleep(0.8)
        await c.shot("H3-標題卡與剪除")
        print("最終例外：",len(c.exc)); [print("  ",e[:300]) for e in c.exc]
asyncio.run(main())
