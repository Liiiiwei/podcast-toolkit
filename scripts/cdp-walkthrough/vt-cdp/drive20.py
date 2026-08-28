import asyncio, json, urllib.request, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for m in ("Page.enable","Runtime.enable","Network.enable"): await c.send(m)
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nc="+str(id(c))}); await asyncio.sleep(3.0)
        # 等影片真的可 seek（python -m http.server 不支援 Range 會卡在 seekable=0）
        for _ in range(30):
            rs=await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState,v.seekable.length]})()")
            if rs and rs[0]>=1 and rs[1]>0: break
            await asyncio.sleep(0.3)
        print("video readyState/seekable：",rs)
        async def mark(step):
            if c.exc:
                print(f">>> exception 在【{step}】："); [print(e[:900]) for e in c.exc]; c.exc.clear()
            else: print(f"    ✓ {step}")
        async def hover_row(i):
            r=await c.ev(f"(()=>{{const e=document.querySelector('.vt-line[data-i=\"{i}\"]');const b=e.getBoundingClientRect();return{{x:b.x+b.width*0.4,y:b.y+b.height/2}}}})()")
            await c.send("Input.dispatchMouseEvent",{"type":"mouseMoved","x":r["x"],"y":r["y"]}); await asyncio.sleep(0.2)
        async def tool_btn(i,label):
            return await c.ev(f"""(()=>{{const row=document.querySelector('.vt-line[data-i="{i}"]');
              const b=Array.from(row.querySelectorAll('.vt-line-tool')).find(x=>x.getAttribute('aria-label')==='{label}');
              if(!b) return null; const r=b.getBoundingClientRect();
              return {{x:r.x+r.width/2,y:r.y+r.height/2,cls:b.className,title:b.title,disabled:b.disabled}}}})()""")

        print("== T0 四顆工具鈕齊備 ==")
        await hover_row(3)
        print("  第4列鈕：", await c.ev("Array.from(document.querySelector('.vt-line[data-i=\"3\"]').querySelectorAll('.vt-line-tool')).map(b=>b.getAttribute(\'aria-label\'))"))
        print("  刪鈕 title：", (await tool_btn(3,"刪"))["title"])
        await mark("T0 鈕齊備")

        print("== T1 點「刪」刪掉整句（不動剪除/總長）==")
        before=await c.ev("__vtSubs()"); st0=await c.ev("__vtStats()")
        target=before[3]["text"]
        b=await tool_btn(3,"刪"); await c.click(b["x"],b["y"]); await asyncio.sleep(0.5)
        after=await c.ev("__vtSubs()"); st1=await c.ev("__vtStats()")
        print(f"  句數 {len(before)} → {len(after)}；被刪的是「{target}」→ 仍在清單：", any(s["text"]==target for s in after))
        print("  總長不變：", st0["duration"]==st1["duration"], " 剪後不變：", st0["finalDuration"]==st1["finalDuration"])
        print("  右欄列數/計數：", await c.ev("[document.querySelectorAll('.vt-line').length, document.getElementById('vt-line-count').textContent]"))
        print("  時間軸塊數：", await c.ev("document.querySelectorAll('#vt-sub-track .vt-sub').length"))
        print("  刪後焦點列：", await c.ev("(()=>{const a=document.activeElement;return a&&a.classList.contains('vt-line-input')?[Number(a.closest('.vt-line').dataset.i),a.value]:null})()"))
        await mark("T1 刪鈕")

        print("== T2 ⌘Z 復原刪除 ==")
        await c.send("Input.dispatchKeyEvent",{"type":"keyDown","key":"z","code":"KeyZ","windowsVirtualKeyCode":90,"nativeVirtualKeyCode":90,"modifiers":4})
        await c.send("Input.dispatchKeyEvent",{"type":"keyUp","key":"z","code":"KeyZ","windowsVirtualKeyCode":90,"nativeVirtualKeyCode":90,"modifiers":4})
        await asyncio.sleep(0.5)
        undone=await c.ev("__vtSubs()")
        print(f"  句數 → {len(undone)}；那句回來了：", any(s["text"]==target for s in undone))
        await mark("T2 復原")

        print("== T3 ⌘⌫ 在輸入框內刪整句（不是刪一個字）==")
        await c.ev("__vtStartEdit(5)"); await asyncio.sleep(0.3)
        t5=(await c.ev("__vtSubs()"))[5]["text"]
        print("  第6句：", t5, " 編輯中值：", await c.ev("document.activeElement.value"))
        for t in ("rawKeyDown","keyUp"):
            await c.send("Input.dispatchKeyEvent",{"type":t,"key":"Backspace","code":"Backspace","windowsVirtualKeyCode":8,"nativeVirtualKeyCode":8,"modifiers":4})
        await asyncio.sleep(0.5)
        a3=await c.ev("__vtSubs()")
        print(f"  句數 → {len(a3)}；那句消失：", not any(s["text"]==t5 for s in a3))
        print("  沒有變成只刪一個字：", all(s["text"]!=t5[:-1] for s in a3))
        await mark("T3 ⌘⌫")

        print("== T4 短拖曳有提示、正常拖曳照剪 ==")
        tl=await c.ev("(()=>{const r=document.getElementById('vt-timeline').getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height}})()")
        dur=(await c.ev("__vtStats()"))["duration"]
        pps=tl["w"]/dur
        print(f"  時間軸寬 {tl['w']:.0f}px / {dur}s = {pps:.1f}px/秒；0.15 秒 = {pps*0.15:.1f}px")
        cuts0=len(await c.ev("__vtStats().cuts") or [])
        x0=tl["x"]+tl["w"]*0.5; y=tl["y"]+tl["h"]/2
        await c.drag(x0,y,x0+4,y,steps=4); await asyncio.sleep(0.5)
        toast=await c.ev("(()=>{const e=document.getElementById('vt-toast');return{hidden:e.hidden,text:e.textContent}})()")
        cuts1=len(await c.ev("__vtStats().cuts") or [])
        print(f"  拖 4px（{4/pps:.3f} 秒）→ 剪除數 {cuts0}→{cuts1}；提示：", toast)
        await c.shot("T4-短拖曳提示")
        await c.drag(x0,y,x0+pps*2,y,steps=8); await asyncio.sleep(0.5)
        cuts2=len(await c.ev("__vtStats().cuts") or [])
        st=await c.ev("__vtStats()")
        print(f"  拖 2 秒 → 剪除數 {cuts1}→{cuts2}；不變式 {st['duration']}−{st['cutTotal']}={st['finalDuration']}：",
              abs(st["duration"]-st["cutTotal"]-st["finalDuration"])<1e-6)
        await asyncio.sleep(2.0)
        print("  提示 2.2 秒後自動收：", await c.ev("document.getElementById('vt-toast').hidden"))
        await mark("T4 短拖曳提示")

        print("== T5 升格後底部字幕不重複顯示 ==")
        subs=await c.ev("__vtSubs()")
        i=next(k for k,s in enumerate(subs) if s["start"]>st["cutTotal"]+3)
        s=subs[i]; mid=(s["start"]+s["end"])/2
        await c.ev(f"(()=>{{const v=document.getElementById('vt-video');v.currentTime={mid};}})()"); await asyncio.sleep(0.6)
        pre0=await c.ev("(()=>{const w=document.getElementById('vt-sub-preview');return{hidden:w.hidden,text:document.getElementById('vt-sub-preview-text').textContent}})()")
        print(f"  升格前（播放頭 {mid:.2f}s，第{i+1}句）字幕預覽：", pre0)
        await c.ev(f"__vtPromote({i})"); await asyncio.sleep(0.6)
        pre1=await c.ev("(()=>{const w=document.getElementById('vt-sub-preview');return{hidden:w.hidden,text:document.getElementById('vt-sub-preview-text').textContent}})()")
        cv=await c.ev("__vtCardView()")
        print("  升格後字幕預覽：", pre1)
        print("  標題卡疊層文字：", (cv or {}).get("text"))
        print("  同一句只出現一次：", pre1["hidden"] is True and bool(cv))
        await c.shot("T5-升格不重複")
        await mark("T5 不重複")

        print("== T6 右欄標記已升格 ==")
        row=await c.ev(f"""(()=>{{const r=document.querySelector('.vt-line[data-i="{i}"]');
          const b=Array.from(r.querySelectorAll('.vt-line-tool')).find(x=>x.getAttribute('aria-label')==='卡');
          return {{carded:r.classList.contains('is-carded'), toolsOpacity:getComputedStyle(r.querySelector('.vt-line-tools')).opacity,
                   btnOn:b.classList.contains('is-on'), btnTitle:b.title}}}})()""")
        print("  該列：", row)
        other=await c.ev(f"""(()=>{{const r=document.querySelector('.vt-line[data-i="{i+1}"]');
          const b=Array.from(r.querySelectorAll('.vt-line-tool')).find(x=>x.getAttribute('aria-label')==='卡');
          return {{carded:r.classList.contains('is-carded'), btnOn:b.classList.contains('is-on')}}}})()""")
        print("  隔壁列（不該被標記）：", other)
        await mark("T6 右欄標記")

        print("== T7 刪掉卡 → 字幕回來 ==")
        # 刪卡出口是卡塊上的 × 鈕（真滑鼠點，不走 hook）
        d=await c.ev("""(()=>{const b=document.querySelector('#vt-card-track .vt-card-del');
          if(!b) return null; const r=b.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}})()""")
        await c.click(d["x"],d["y"]); await asyncio.sleep(0.6)
        print("  卡數：", len(await c.ev("__vtCards()")))
        await c.ev(f"(()=>{{const v=document.getElementById('vt-video');v.currentTime={mid};}})()"); await asyncio.sleep(0.6)
        pre2=await c.ev("(()=>{const w=document.getElementById('vt-sub-preview');return{hidden:w.hidden,text:document.getElementById('vt-sub-preview-text').textContent}})()")
        print("  字幕預覽回來：", pre2)
        print("  右欄標記已清：", await c.ev(f"!document.querySelector('.vt-line[data-i=\"{i}\"]').classList.contains('is-carded')"))
        await mark("T7 刪卡還原")

        print("== T8 回歸：斷句/合併/句數對列數 ==")
        n0=len(await c.ev("__vtSubs()"))
        await c.ev("__vtStartEdit(2)"); await asyncio.sleep(0.3)
        await c.key("Home",36)
        for _ in range(5): await c.key("ArrowRight",39)
        await c.key("Enter",13); await asyncio.sleep(0.5)
        n1=len(await c.ev("__vtSubs()"))
        await c.key("Home",36); await c.key("Backspace",8); await asyncio.sleep(0.5)
        n2=len(await c.ev("__vtSubs()"))
        print(f"  句數 {n0} →斷句→ {n1} →合併→ {n2}（應為 {n0}/{n0+1}/{n0}）")
        print("  句數==列數：", n2==await c.ev("document.querySelectorAll('.vt-line').length"))
        st=await c.ev("__vtStats()")
        print(f"  不變式 {st['duration']}−{st['cutTotal']}={st['finalDuration']}：", abs(st["duration"]-st["cutTotal"]-st["finalDuration"])<1e-6)
        await c.shot("T8-總覽")
        await mark("T8 回歸")
        print("== 最終 console exception：", len(c.exc))
        for e in c.exc: print("   ",e[:400])
asyncio.run(main())
