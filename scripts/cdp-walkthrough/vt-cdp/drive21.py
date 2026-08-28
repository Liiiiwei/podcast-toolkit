import asyncio, json, urllib.request, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable"); await c.send("Network.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))}); await asyncio.sleep(3.0)
        for _ in range(30):
            r=await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState, v.seekable.length]})()")
            if r and r[0]>=1 and r[1]>0: break
            await asyncio.sleep(0.3)
        print("video readyState/seekable：", r)
        async def mark(step):
            if c.exc:
                print(f">>> exception 在【{step}】："); [print(e[:900]) for e in c.exc]; c.exc.clear()
            else: print(f"    ✓ {step}")

        async def blk(i=0):
            return await c.ev(f"""(()=>{{const e=document.querySelectorAll('#vt-card-track .vt-card')[{i}];
              if(!e) return null; const r=e.getBoundingClientRect();
              return {{x:r.x,y:r.y+r.height/2,w:r.width,cx:r.x+r.width/2,left:r.x,right:r.x+r.width}}}})()""")
        async def cards():
            return await c.ev("__vtCards().map(c=>({id:c.id,s:+c.start.toFixed(3),e:+c.end.toFixed(3),d:+(c.end-c.start).toFixed(3)}))")
        pxps=await c.ev("(()=>{const r=document.getElementById('vt-card-track').getBoundingClientRect();return r.width/__vtStats().duration})()")

        print("== U0 卡塊有兩個邊界把手 ==")
        await c.ev("__vtPromote(3)"); await asyncio.sleep(0.4)
        print("  卡：", await cards())
        print("  把手數：", await c.ev("document.querySelectorAll('#vt-card-track .vt-card .vt-card-h').length"),
              " 類別：", await c.ev("Array.from(document.querySelectorAll('#vt-card-track .vt-card .vt-card-h')).map(n=>n.className)"))
        print("  title：", (await c.ev("document.querySelector('#vt-card-track .vt-card').title")).replace("\n","｜"))
        print(f"  時間軸換算 {pxps:.2f} px/秒")
        await mark("U0 把手存在")

        print("== U1 拖整塊平移：長度不變 ==")
        b=await blk(); before=(await cards())[0]
        await c.drag(b["cx"],b["y"],b["cx"]+pxps*2.0,b["y"],steps=12); await asyncio.sleep(0.4)
        after=(await cards())[0]
        print("  拖 +2.0 秒：", before, "→", after)
        print("  位移約 2 秒：", abs((after["s"]-before["s"])-2.0)<0.12, " 長度不變：", abs(after["d"]-before["d"])<1e-3)
        await mark("U1 平移")

        print("== U2 拖右把手改出點 ==")
        b=await blk(); before=(await cards())[0]
        await c.drag(b["right"]-3,b["y"],b["right"]-3+pxps*1.5,b["y"],steps=12); await asyncio.sleep(0.4)
        after=(await cards())[0]
        print("  拖右 +1.5 秒：", before, "→", after)
        print("  進點不動：", abs(after["s"]-before["s"])<1e-6, " 出點 +1.5：", abs((after["e"]-before["e"])-1.5)<0.12)
        await mark("U2 右把手")

        print("== U3 拖左把手改進點 ==")
        b=await blk(); before=(await cards())[0]
        await c.drag(b["left"]+3,b["y"],b["left"]+3-pxps*1.0,b["y"],steps=12); await asyncio.sleep(0.4)
        after=(await cards())[0]
        print("  拖左 −1.0 秒：", before, "→", after)
        print("  出點不動：", abs(after["e"]-before["e"])<1e-6, " 進點 −1.0：", abs((before["s"]-after["s"])-1.0)<0.12)
        await mark("U3 左把手")

        print("== U4 縮到極短：不短於 0.3 秒 ==")
        b=await blk()
        await c.drag(b["left"]+3,b["y"],b["right"]+pxps*2,b["y"],steps=14); await asyncio.sleep(0.4)
        a1=(await cards())[0]; print("  左把手往右拖過頭 →", a1, " 不短於 0.3：", a1["d"]>=0.3-1e-6)
        b=await blk()
        await c.drag(b["right"]-3,b["y"],b["left"]-pxps*2,b["y"],steps=14); await asyncio.sleep(0.4)
        a2=(await cards())[0]; print("  右把手往左拖過頭 →", a2, " 不短於 0.3：", a2["d"]>=0.3-1e-6)
        await mark("U4 最短長度")

        print("== U5 拖出邊界：夾在 0–總長 ==")
        st=await c.ev("__vtStats()")
        b=await blk()
        await c.drag(b["cx"],b["y"],b["cx"]-pxps*40,b["y"],steps=14); await asyncio.sleep(0.4)
        a1=(await cards())[0]; print("  往左拖出頭 →", a1, " start>=0：", a1["s"]>=-1e-6)
        b=await blk()
        await c.drag(b["cx"],b["y"],b["cx"]+pxps*60,b["y"],steps=14); await asyncio.sleep(0.4)
        a2=(await cards())[0]; print(f"  往右拖出頭 → {a2}  end<=總長{st['duration']}：", a2["e"]<=st["duration"]+1e-6, " 長度仍不變：", abs(a2["d"]-a1["d"])<1e-3)
        await mark("U5 邊界夾制")

        print("== U6 ⌘Z：整段拖曳只還原一步 ==")
        # 重新拉回一個乾淨可辨識的長度後拖一次
        await c.ev("(()=>{const c=__vtCards()[0];__vtSetCard(c.id,{});})()")
        b=await blk(); before=(await cards())[0]
        await c.drag(b["cx"],b["y"],b["cx"]-pxps*3.0,b["y"],steps=14); await asyncio.sleep(0.4)
        mid=(await cards())[0]
        await c.ev("document.getElementById('vt-timeline').focus?.()")
        await c.send("Input.dispatchKeyEvent",{"type":"rawKeyDown","key":"z","code":"KeyZ","windowsVirtualKeyCode":90,"nativeVirtualKeyCode":90,"modifiers":4})
        await c.send("Input.dispatchKeyEvent",{"type":"keyUp","key":"z","code":"KeyZ","windowsVirtualKeyCode":90,"nativeVirtualKeyCode":90,"modifiers":4})
        await asyncio.sleep(0.5)
        back=(await cards())[0]
        print("  拖前", before, "→ 拖後", mid, "→ ⌘Z", back)
        print("  一次 ⌘Z 回到拖前：", abs(back["s"]-before["s"])<1e-6 and abs(back["e"]-before["e"])<1e-6)
        await mark("U6 復原")

        print("== U7 拖卡不會誤啟動剪除選區、也沒誤刪 ==")
        st1=await c.ev("__vtStats()")
        b=await blk()
        await c.drag(b["cx"],b["y"],b["cx"]+pxps*1.0,b["y"],steps=10); await asyncio.sleep(0.4)
        st2=await c.ev("__vtStats()")
        print("  剪除數", st1["cutTotal"], "→", st2["cutTotal"], " 卡數：", len(await cards()),
              " 不變式：", abs(st2["duration"]-st2["cutTotal"]-st2["finalDuration"])<1e-6)
        await mark("U7 不誤觸")

        print("== U8 卡被拖離原句 → 右欄「已升格」標記留著（血緣），但字幕要回來 ==")
        marks=await c.ev("""(()=>{const rows=[...document.querySelectorAll('.vt-line')];
          const on=rows.filter(r=>r.classList.contains('is-carded')).map(r=>+r.dataset.i);
          return {carded:on, btnOn:document.querySelectorAll('.vt-line-tool.is-card.is-on').length}})()""")
        print("  拖走後標記：", marks, "（血緣不會因為拖動而斷，標記留著）")
        sub3=await c.ev("(()=>{const s=__vt.subs[3];return [s.start,s.end,s.text]})()")
        await c.ev("__vtSeek(%f)"%((sub3[0]+sub3[1])/2)); await asyncio.sleep(0.5)
        pv=await c.ev("""(()=>{const w=document.getElementById('vt-sub-preview');
          return {hidden:w.hidden, text:document.getElementById('vt-sub-preview-text').textContent}})()""")
        print("  播放頭回原句中點 →", pv)
        print("  U8 斷言：標記仍在", marks["carded"]==[3],
              "　字幕回來", pv["hidden"] is False and pv["text"]==sub3[2])
        await c.ev("__vtPromote(1)"); await asyncio.sleep(0.4)
        m2=await c.ev("""(()=>{const rows=[...document.querySelectorAll('.vt-line')];
          return rows.filter(r=>r.classList.contains('is-carded')).map(r=>+r.dataset.i)})()""")
        print("  再升格第2句 → 標記：", m2, " 卡數：", len(await cards()))
        await c.shot("U-卡塊拖曳")
        await mark("U8 標記聯動")
        print("== 最終 console exception：", len(c.exc))
        for e in c.exc: print("   ",e[:400])
asyncio.run(main())
