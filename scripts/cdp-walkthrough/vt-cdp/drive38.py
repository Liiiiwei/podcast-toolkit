import asyncio, os, time
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
URL="http://127.0.0.1:8877/static/video-edit-prototype.html?demo=1"
OUT="/private/tmp/pt-e2e/20260825 端對端測試/03_成品/端對端測試_YT完整版.preview.mp4"
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))})
        for _ in range(30):
            await asyncio.sleep(0.5)
            if await c.ev("typeof window.__vtPlanPush")=="function": break
        else: raise SystemExit("來源未同步")
        await c.ev("window.confirm=()=>true")
        # 改一句字，讓成品畫面上看得出這是這一輪送出的
        await c.ev("__vtAddCut(3.9,6.0)")
        await c.ev("(()=>{__vt.subs[0].text='這句是從 UI 按鈕送出的';render()})()"); await asyncio.sleep(0.4)
        b=await c.ev("(()=>{const r=document.getElementById('vt-plan-open').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(b["x"],b["y"]); await asyncio.sleep(0.6)
        old = os.path.getmtime(OUT) if os.path.exists(OUT) else 0
        rb=await c.ev("(()=>{const r=document.getElementById('vt-plan-render').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(rb["x"],rb["y"])
        for _ in range(40):
            d=await c.ev("__vtPlanDialog()")
            if not d["busy"]: break
            await asyncio.sleep(0.3)
        print("  送出訊息：",d["msg"],"　色：",d["msgKind"])
        await c.shot("N-UI送出並合成")
        print("  等合成…")
        for _ in range(180):
            await asyncio.sleep(2)
            if os.path.exists(OUT) and os.path.getmtime(OUT)>old+1:
                s1=os.path.getsize(OUT); await asyncio.sleep(3)
                if os.path.getsize(OUT)==s1: break
        print("  成品：", os.path.getsize(OUT), "bytes　更新時間比送出前新：", os.path.getmtime(OUT)>old)
asyncio.run(main())
