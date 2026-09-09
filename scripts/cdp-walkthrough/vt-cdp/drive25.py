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
        print("video readyState/seekable：", st)

        async def cuts(): return (await c.ev("__vtStats()"))["cuts"]
        async def inv():
            s=await c.ev("__vtStats()")
            return abs(s["duration"]-s["cutTotal"]-s["finalDuration"])<1e-6
        async def handle(i, side):
            return await c.ev(f"""(()=>{{const el=document.querySelectorAll('#vt-timeline .vt-cut')[{i}].querySelector('.vt-cut-h.is-{side}');
              if(!el) return null; const r=el.getBoundingClientRect(); return {{x:r.x+r.width/2,y:r.y+r.height/2}}}})()""")
        tlw=await c.ev("document.getElementById('vt-timeline').clientWidth")
        dur=(await c.ev("__vtStats()"))["duration"]
        pxs=tlw/dur
        print(f"時間軸 {tlw}px / {dur}s = {pxs:.2f} px/秒")

        await c.ev("__vtAddCut(4.0, 7.0)"); await asyncio.sleep(0.3)
        hs=await c.ev("""Array.from(document.querySelectorAll('#vt-timeline .vt-cut .vt-cut-h')).map(n=>n.className)""")
        ttl=await c.ev("document.querySelector('#vt-timeline .vt-cut').title")
        print(f"【W0】把手數 {len(hs)} {hs}")
        print("     title：", ttl.replace("\n","｜"))

        h=await handle(0,"r"); await c.drag(h["x"],h["y"],h["x"]+2*pxs,h["y"],steps=8); await asyncio.sleep(0.4)
        cu=await cuts(); print(f"【W1】右把手 +2 秒 → {cu}　起點不動 {abs(cu[0][0]-4.0)<0.06}　終點約 9 {abs(cu[0][1]-9.0)<0.12}　不變式 {await inv()}")

        h=await handle(0,"l"); await c.drag(h["x"],h["y"],h["x"]-1.5*pxs,h["y"],steps=8); await asyncio.sleep(0.4)
        cu=await cuts(); print(f"【W2】左把手 −1.5 秒 → {cu}　終點不動 {abs(cu[0][1]-9.0)<0.12}　起點約 2.5 {abs(cu[0][0]-2.5)<0.12}")

        h=await handle(0,"r"); await c.drag(h["x"],h["y"],h["x"]-20*pxs,h["y"],steps=12); await asyncio.sleep(0.4)
        cu=await cuts(); d=cu[0][1]-cu[0][0]
        print(f"【W3】右把手往左拖過頭 → 長度 {d:.3f}　不短於 0.15 {d>=0.149}")

        # 重來：兩段剪除測鄰段夾制
        await c.ev("__vtUndo && __vtUndo()")
        await c.ev("(()=>{__vt.cuts.length=0;})()")
        await c.ev("__vtAddCut(4.0, 6.0)"); await c.ev("__vtAddCut(10.0, 12.0)"); await asyncio.sleep(0.4)
        print("【W4】兩段：", await cuts())
        h=await handle(0,"r"); await c.drag(h["x"],h["y"],h["x"]+8*pxs,h["y"],steps=12); await asyncio.sleep(0.4)
        cu=await cuts()
        print(f"     左段右把手往右拖過頭 → {cu}　停在 9.85（鄰段10−0.15）{abs(cu[0][1]-9.85)<0.06}　仍是兩段 {len(cu)==2}　不變式 {await inv()}")

        h=await handle(0,"l"); await c.drag(h["x"],h["y"],h["x"]-20*pxs,h["y"],steps=12); await asyncio.sleep(0.4)
        cu=await cuts(); print(f"【W5】左段左把手拖出頭 → 起點 {cu[0][0]}（期待 0）")
        h=await handle(1,"r"); await c.drag(h["x"],h["y"],h["x"]+20*pxs,h["y"],steps=12); await asyncio.sleep(0.4)
        cu=await cuts(); print(f"     右段右把手拖出頭 → 終點 {cu[1][1]}（期待 {dur}）　不變式 {await inv()}")

        before=await cuts()
        # 往右拖：左段已頂到 9.85，右段左把手往左會被夾住不動，測不到還原
        h=await handle(1,"l"); await c.drag(h["x"],h["y"],h["x"]+3*pxs,h["y"],steps=10); await asyncio.sleep(0.4)
        mid=await cuts()
        for t in ("rawKeyDown","keyUp"):
            await c.send("Input.dispatchKeyEvent",{"type":t,"key":"z","code":"KeyZ","windowsVirtualKeyCode":90,"nativeVirtualKeyCode":90,"modifiers":4})
        await asyncio.sleep(0.5)
        aft=await cuts()
        print(f"【W6】拖前 {before[1]} → 拖後 {mid[1]} → ⌘Z {aft[1]}　真的有動 {abs(mid[1][0]-before[1][0])>1}　一次還原 {aft==before}")

        n0=len(await cuts())
        el=await c.ev("(()=>{const r=document.querySelectorAll('#vt-timeline .vt-cut')[0].getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(el["x"],el["y"]); await asyncio.sleep(0.4)
        print(f"【W7】點段中央取消 → 段數 {n0}→{len(await cuts())}（期待 −1）")
        # 拖把手放開不該順便取消
        n1=len(await cuts())
        h=await handle(0,"r"); await c.drag(h["x"],h["y"],h["x"]+1*pxs,h["y"],steps=8); await asyncio.sleep(0.4)
        print(f"     拖把手後段數 {n1}→{len(await cuts())}（不該被取消）")

        await c.ev("(()=>{__vt.cuts.length=0;})()"); await c.ev("__vtAddCut(3.0,3.2)"); await asyncio.sleep(0.4)
        w=await c.ev("(()=>{const r=document.querySelector('#vt-timeline .vt-cut').getBoundingClientRect();return Math.round(r.width)})()")
        hn=await c.ev("document.querySelectorAll('#vt-timeline .vt-cut .vt-cut-h').length")
        print(f"【W8】0.2 秒窄段 寬 {w}px → 把手數 {hn}（期待 0，太窄不畫）")
        zin=await c.ev("(()=>{const b=document.getElementById('vt-zoom-in').getBoundingClientRect();return{x:b.x+b.width/2,y:b.y+b.height/2}})()")
        for _ in range(3):
            await c.click(zin["x"],zin["y"]); await asyncio.sleep(0.4)
            if await c.ev("document.querySelectorAll('#vt-timeline .vt-cut .vt-cut-h').length"): break
        w2=await c.ev("(()=>{const r=document.querySelector('#vt-timeline .vt-cut').getBoundingClientRect();return Math.round(r.width)})()")
        print(f"     放大後 寬 {w2}px → 把手數 {await c.ev('document.querySelectorAll(\"#vt-timeline .vt-cut .vt-cut-h\").length')}（出路存在）")

        for _ in range(12):
            if await c.ev("document.getElementById('vt-zoom-out').disabled"): break
            await c.ev("document.getElementById('vt-zoom-out').click()"); await asyncio.sleep(0.15)
        await asyncio.sleep(0.4)
        await c.ev("(()=>{__vt.cuts.length=0;})()"); await c.ev("__vtAddCut(4.0,6.0)"); await asyncio.sleep(0.4)
        st0=await c.ev("__vtSubs()")
        h=await handle(0,"r"); await c.drag(h["x"],h["y"],h["x"]+4*pxs,h["y"],steps=10); await asyncio.sleep(0.5)
        marks=await c.ev("__vtLines().map(l=>l.cut)")
        print(f"【W9】拖長剪除後 右欄被標為剪除的列數 {sum(1 for m in marks if m)}（應 >0）：", marks)
        print(f"     不變式 {await inv()}　剪除 {await cuts()}")
        await c.shot("W-剪除段微調")
        print("例外：", len(c.exc))
        for e in c.exc: print("  ", e[:400])
asyncio.run(main())
