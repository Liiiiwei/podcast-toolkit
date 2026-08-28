# 頂列新 UI（試看 30 秒鈕 + 輸出目標 chip）的目視驗收：三態各截一張
import asyncio, json, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
URL="http://127.0.0.1:8877/static/video-edit-prototype.html?v=40"

CK=("(()=>{const e=document.querySelector('#vt-targets input[value=\"%s\"]');"
    "const r=e.parentElement.getBoundingClientRect();"
    "return{x:r.x+r.width/2,y:r.y+r.height/2,on:e.checked}})()")

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL}); await asyncio.sleep(2.0)
        for _ in range(50):                      # hook 出現才算載好，固定 sleep 不夠
            if await c.ev("typeof __vtRender==='function'"): break
            await asyncio.sleep(0.3)
        # 版本指紋：確認服務的是含新 UI 的版本，不是舊快取
        fp=await c.ev("(()=>{const p=document.getElementById('vt-preview'),t=document.getElementById('vt-targets');"
                      "return{preview:p&&p.textContent.trim(),targets:t?[...t.querySelectorAll('input')].map(e=>e.value):null}})()")
        print("版本指紋：",fp)
        assert fp["preview"]=="試看 30 秒" and fp["targets"]==["yt","reels","mp3"], "來源未同步"

        async def bar(name):
            r=await c.ev("(()=>{const b=document.querySelector('.vt-topbar')||document.getElementById('vt-targets').closest('header,div');"
                         "const q=b.getBoundingClientRect();return{x:q.x,y:q.y,w:q.width,h:q.height}})()")
            img=await c.send("Page.captureScreenshot",{"format":"png","clip":{
                "x":max(0,r["x"]-4),"y":max(0,r["y"]-4),"width":r["w"]+8,"height":r["h"]+8,"scale":2}})
            open(f"{SHOTS}/{name}.png","wb").write(base64.b64decode(img["data"]))
            print("  截圖：",name,"頂列尺寸",round(r["w"]),"x",round(r["h"]))

        await bar("O1-頂列-預設只勾YT")
        b=await c.ev(CK%"reels"); await c.click(b["x"],b["y"]); await asyncio.sleep(0.4)
        await bar("O2-頂列-加勾Reels")
        for v in ("yt","reels"):
            b=await c.ev(CK%v)
            if b["on"]: await c.click(b["x"],b["y"]); await asyncio.sleep(0.3)
        st=await c.ev("(()=>({targets:[...document.querySelectorAll('#vt-targets input:checked')].map(e=>e.value),"
                      "red:document.getElementById('vt-targets').classList.contains('is-empty'),"
                      "exp:document.getElementById('vt-export').disabled,"
                      "pv:document.getElementById('vt-preview').disabled,"
                      "msg:document.getElementById('vt-export-msg').textContent}))()")
        print("  全不選態：",st)
        await bar("O3-頂列-全不選-鈕鎖住紅框")
        print("例外：",len(c.exc))
asyncio.run(main())
