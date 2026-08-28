import asyncio, json, urllib.request, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL}); await asyncio.sleep(3.5)
        print("== G 三版型外觀（各截一張）==")
        await c.ev("__vtPromote(2)")
        for tpl,name in [("big","大字報"),("lower","下標條"),("quote","引言框")]:
            r=await c.ev(f"(()=>{{const t=document.querySelector('#vt-tpl-grid .vt-tpl[data-tpl=\\'{tpl}\\']');const r=t.getBoundingClientRect();return {{x:r.x+r.width/2,y:r.y+r.height/2}}}})()")
            await c.click(r["x"],r["y"]); await asyncio.sleep(0.4)
            cd=(await c.ev("__vtCards()"))[0]
            print(f"    （真滑鼠點縮圖）state: tpl={cd['tpl']} x={cd['x']} y={cd['y']}")
            v=await c.ev("__vtCardView()")
            print(f"  {name:4s} cls={v['cls']:34s} fontSize={v['fontSize']:>8s} left={v['left']} top={v['top']}")
            await c.shot(f"D-版型-{name}")
        print("== H Shift 不吸附 ==")
        await c.ev("__vtSetCard({x:0.5,y:0.5,tpl:'big'})"); await asyncio.sleep(0.3)
        g=await c.ev("(()=>{const e=document.querySelector('#vt-card-layer .vt-card-view');const r=e.getBoundingClientRect();const l=document.getElementById('vt-card-layer').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,lx:l.x,ly:l.y,lw:l.width,lh:l.height}})()")
        tx=g["lx"]+g["lw"]*0.52; ty=g["ly"]+g["lh"]*0.19
        # 按住 Shift 拖
        await c.send("Input.dispatchMouseEvent",{"type":"mouseMoved","x":g["x"],"y":g["y"]})
        await c.send("Input.dispatchMouseEvent",{"type":"mousePressed","x":g["x"],"y":g["y"],"button":"left","buttons":1,"clickCount":1,"modifiers":8})
        for k in range(1,9):
            await c.send("Input.dispatchMouseEvent",{"type":"mouseMoved","x":g["x"]+(tx-g["x"])*k/8,"y":g["y"]+(ty-g["y"])*k/8,"button":"left","buttons":1,"modifiers":8})
            await asyncio.sleep(0.02)
        await c.send("Input.dispatchMouseEvent",{"type":"mouseReleased","x":tx,"y":ty,"button":"left","buttons":0,"clickCount":1,"modifiers":8})
        await asyncio.sleep(0.4)
        cd=(await c.ev("__vtCards()"))[0]
        print(f"  按住 Shift 拖到 0.52/0.19 → x={cd['x']:.4f} y={cd['y']:.4f}（應維持 0.52/0.19 不吸）")
        print("== I 回歸：字幕就地編輯 + 剪除不變式 ==")
        b=await c.ev("(()=>{const s=document.querySelectorAll('#vt-sub-track .vt-sub')[0];const r=s.getBoundingClientRect();return {x:r.x+8,y:r.y+r.height/2}})()")
        await c.click(b["x"],b["y"]); await asyncio.sleep(0.4)
        print("  聚焦輸入框：", await c.ev("document.activeElement.className"))
        await c.type("回歸測試"); await c.key("Enter",13); await asyncio.sleep(0.4)
        print("  第1句：", (await c.ev("__vtSubs()"))[0]["text"])
        st0=await c.ev("__vtStats()")
        await c.ev("__vtAddCut(3.0,5.0)"); await asyncio.sleep(0.4)
        st1=await c.ev("__vtStats()")
        print(f"  剪除前 {st0['finalText']} → 剪除後 {st1['finalText']}；不變式 {st1['duration']:.1f}-{st1['cutTotal']:.1f}={st1['finalDuration']:.1f} → {abs(st1['duration']-st1['cutTotal']-st1['finalDuration'])<0.001}")
        await c.shot("D-回歸總覽")
        print("== console exception：", len(c.exc))
        for e in c.exc: print("   ",e[:400])
asyncio.run(main())
