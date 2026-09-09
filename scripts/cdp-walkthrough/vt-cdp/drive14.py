import asyncio, json, urllib.request, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable"); await c.send("Network.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))}); await asyncio.sleep(3.5)
        async def mark(step):
            if c.exc:
                print(f">>> exception 在【{step}】："); [print(e[:900]) for e in c.exc]; c.exc.clear()
            else: print(f"    ✓ {step}")

        print("== L 軌道順序（上→下）與波形高度 ==")
        print("  軌名：", await c.ev("Array.from(document.querySelectorAll('.vt-gutter-cell')).map(n=>n.textContent.trim())"))
        geo=await c.ev("""(()=>{const g=id=>{const r=document.getElementById(id).getBoundingClientRect();return {top:Math.round(r.top),h:Math.round(r.height)}};
          return {card:g('vt-card-track'),sub:g('vt-sub-track'),wave:g('vt-timeline')}})()""")
        print("  標題卡軌 top/h：",geo['card'],"　字幕軌：",geo['sub'],"　波形：",geo['wave'])
        print("  上下順序正確（標題卡<字幕<波形）：", geo['card']['top']<geo['sub']['top']<geo['wave']['top'])
        print("  波形高度 64：", geo['wave']['h']==64)
        await mark("L 軌道順序")

        print("== M 剪除段數已移除 ==")
        print("  #vt-stat-count 存在：", await c.ev("!!document.getElementById('vt-stat-count')"))
        print("  頂部統計項：", await c.ev("Array.from(document.querySelectorAll('.vt-stat-label')).map(n=>n.textContent.trim())"))
        await mark("M 剪除段數")

        print("== N 九宮格已移除 + 拖曳不吸附 ==")
        print("  #ct-pos 存在：", await c.ev("!!document.getElementById('ct-pos')"),
              "　.vt-snap-guide 數：", await c.ev("document.querySelectorAll('.vt-snap-guide').length"))
        await c.ev("__vtPromote(2)")
        await c.ev("(()=>{const v=document.getElementById('vt-video');v.currentTime=5.0;})()")
        # 影片 seek 是非同步的，疊層要等 timeupdate 才畫得出來 → 輪詢等它出現
        for _ in range(20):
            if await c.ev("!!document.querySelector('#vt-card-layer .vt-card-view')"): break
            await c.ev("(()=>{const v=document.getElementById('vt-video');v.currentTime=5.0;})()")
            await asyncio.sleep(0.3)
        print("  回預設位鈕存在：", await c.ev("!!document.getElementById('ct-reset-pos')"),
              "　疊層已出現：", await c.ev("!!document.querySelector('#vt-card-layer .vt-card-view')"))
        box=await c.ev("(()=>{const r=document.getElementById('vt-card-layer').getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height}})()")
        el=await c.ev("(()=>{const r=document.querySelector('#vt-card-layer .vt-card-view').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        tx,ty=box["x"]+box["w"]*0.52, box["y"]+box["h"]*0.19
        await c.drag(el["x"],el["y"],tx,ty,steps=10); await asyncio.sleep(0.4)
        cd=(await c.ev("__vtCards()"))[0]
        print(f"  拖到 0.52/0.19 放開後 → x={cd['x']:.4f} y={cd['y']:.4f}（不應被吸附成 0.5/0.16）")
        await c.shot("F1-拖曳不吸附")
        await mark("N 拖曳不吸附")

        print("== O 固定預設位 + 回預設位 ==")
        r=await c.ev("(()=>{const b=document.getElementById('ct-reset-pos').getBoundingClientRect();return{x:b.x+b.width/2,y:b.y+b.height/2}})()")
        await c.click(r["x"],r["y"]); await asyncio.sleep(0.3)
        cd=(await c.ev("__vtCards()"))[0]
        print(f"  點「回版型預設位」後 → x={cd['x']:.4f} y={cd['y']:.4f}（big 預設 0.5/0.5）")
        n2=await c.ev("__vtDropTpl('lower', 12.0)")
        n3=await c.ev("__vtDropTpl('quote', 18.0)")
        print(f"  新建下標條 → x={n2['x']} y={n2['y']}（預設 0.2/0.84）")
        print(f"  新建引言框 → x={n3['x']} y={n3['y']}（預設 0.5/0.5）")
        await mark("O 預設位")

        print("== P 字幕斷句（真鍵盤 Enter）==")
        before=await c.ev("__vtSubs()")
        print("  斷句前句數：",len(before),"　第3句：",before[2]["text"],f"{before[2]['start']:.2f}–{before[2]['end']:.2f}")
        pos=await c.ev("(()=>{const e=document.querySelectorAll('#vt-sub-track .vt-sub')[2].getBoundingClientRect();return{x:e.x+e.width/2,y:e.y+e.height/2}})()")
        await c.click(pos["x"],pos["y"]); await asyncio.sleep(0.4)
        eb=await c.ev("__vtEditBox()")
        print("  編輯框：", {k:eb[k] for k in ("left","value","focused")}, " 小鈕：", eb["tools"])
        await c.key("Home",36); await asyncio.sleep(0.1)
        for _ in range(8): await c.key("ArrowRight",39)
        await asyncio.sleep(0.2)
        print("  游標位置：", (await c.ev("__vtEditBox()"))["caret"])
        await c.key("Enter",13); await asyncio.sleep(0.5)
        after=await c.ev("__vtSubs()")
        print("  斷句後句數：",len(after))
        for k in (2,3): print(f"   第{k+1}句：{after[k]['text']}　{after[k]['start']:.2f}–{after[k]['end']:.2f}")
        print("  時間封套未超出原句：", after[2]["start"]==before[2]["start"] and after[3]["end"]<=before[2]["end"]+1e-9)
        print("  斷句後仍在編輯：", await c.ev("__vtEditBox()"))
        await c.shot("F2-斷句後")
        await mark("P 斷句")

        print("== Q 合併（句首 Backspace）==")
        await c.key("Home",36); await asyncio.sleep(0.1)
        await c.key("Backspace",8); await asyncio.sleep(0.5)
        merged=await c.ev("__vtSubs()")
        print("  合併後句數：",len(merged),"　第3句：",merged[2]["text"],f"{merged[2]['start']:.2f}–{merged[2]['end']:.2f}")
        print("  還原成原句：", merged[2]["text"]==before[2]["text"] and abs(merged[2]["end"]-before[2]["end"])<1e-9)
        print("  游標停在接縫：", (await c.ev("__vtEditBox()"))["caret"])
        await mark("Q 合併")

        print("== R 第一句不可合併 ==")
        await c.ev("__vtStartEdit(0)"); await asyncio.sleep(0.3)
        print("  第一句小鈕：", (await c.ev("__vtEditBox()"))["tools"])
        await c.key("Backspace",8); await asyncio.sleep(0.3)
        print("  按 Backspace 後句數不變：", len(await c.ev("__vtSubs()"))==len(merged))
        await c.key("Escape",27); await asyncio.sleep(0.3)
        await mark("R 邊界")

        print("== S 回歸：剪除不變式 + 升格 ==")
        await c.ev("__vtAddCut(3.0,5.0)"); await asyncio.sleep(0.3)
        st=await c.ev("__vtStats()")
        print(f"  原長 {st['duration']} − 剪除 {st['cutTotal']} = 剪後 {st['finalDuration']}　不變式：",
              abs(st['duration']-st['cutTotal']-st['finalDuration'])<1e-6)
        print("  標題卡數：", len(await c.ev("__vtCards()")))
        await c.shot("F3-總覽")
        await mark("S 回歸")
        print("== 最終 console exception：", len(c.exc))
        for e in c.exc: print("   ",e[:400])
asyncio.run(main())
