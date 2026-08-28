import asyncio, json
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))}); await asyncio.sleep(3.5)

        # 媒體可 seek 才算環境正常（不支援 Range 的伺服器會讓 currentTime 靜默無效）
        for _ in range(60):
            r=await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState,v.seekable.length]})()")
            if r[0]>=1 and r[1]>0: break
            await asyncio.sleep(0.5)
        print("video readyState/seekable：",r)
        if not (r[0]>=1 and r[1]>0):
            raise SystemExit("影片不可 seek，這輪測到的 currentTime 不算數（伺服器沒支援 Range 或還沒載完）")

        async def blur(): await c.ev("document.activeElement && document.activeElement.blur()")
        async def now(): return round(await c.ev("document.getElementById('vt-video').currentTime"),4)
        async def toast(): return await c.ev("(()=>{const t=document.getElementById('vt-toast');return t.hidden?'':t.textContent})()")
        async def tap(k,vk,code,mods=0,n=1):
            for _ in range(n):
                for t in ("rawKeyDown","keyUp"):
                    await c.send("Input.dispatchKeyEvent",{"type":t,"key":k,"code":code,
                        "windowsVirtualKeyCode":vk,"nativeVirtualKeyCode":vk,"modifiers":mods})
                await asyncio.sleep(0.06)
            await asyncio.sleep(0.15)
        LEFT =lambda n=1,mods=0: tap("ArrowLeft",37,"ArrowLeft",mods,n)
        RIGHT=lambda n=1,mods=0: tap("ArrowRight",39,"ArrowRight",mods,n)
        I   =lambda: tap("i",73,"KeyI")
        O   =lambda: tap("o",79,"KeyO")
        BS  =lambda: tap("Backspace",8,"Backspace")
        ESC =lambda: tap("Escape",27,"Escape")
        UNDO=lambda: tap("z",90,"KeyZ",4)

        print("【Z0】版本指紋與初始狀態")
        print("  __vtMark：", await c.ev("typeof window.__vtMark"),
              "　__vtSelectionView：", await c.ev("typeof window.__vtSelectionView"))
        print("  初始 mark：", await c.ev("__vtMark()"),
              "　選區元素：", await c.ev("__vtSelectionView()"))
        hint=await c.ev("document.querySelector('.vt-tl-hint').title")
        print("  ⓘ 提示含快捷鍵：", all(k in hint for k in ("I 進點","O 出點","⌫ 剪除","Esc")))

        print("【Z1】← → 移動播放頭（⇧ 跨大步）")
        await blur(); await c.ev("__vtSeek(10.0)"); await asyncio.sleep(0.3)
        t0=await now(); await RIGHT(n=3); t1=await now()
        await LEFT(); t2=await now()
        await RIGHT(mods=8); t3=await now()
        await LEFT(mods=8); t4=await now()
        print(f"  10.0 →3×→ {t1}（期待 10.3）　→1×← {t2}（期待 10.2）")
        print(f"  ⇧→ {t3}（期待 11.2）　⇧← {t4}（期待 10.2）")
        print("  小步 0.1／大步 1.0：",
              abs(t1-10.3)<1e-6 and abs(t2-10.2)<1e-6 and abs(t3-11.2)<1e-6 and abs(t4-10.2)<1e-6)
        await c.ev("__vtSeek(0)"); await asyncio.sleep(0.25); await LEFT(n=3)
        b0=await now()
        await c.ev("__vtSeek(24)"); await asyncio.sleep(0.25); await RIGHT(n=3)
        b1=await now()
        print(f"  邊界：0 按 ←×3 → {b0}（期待 0）；尾端按 →×3 → {b1}（期待 24）")

        print("【Z2】I 標進點、O 標出點 → 選區出現")
        await c.ev("__vtSeek(5.0)"); await asyncio.sleep(0.3); await I()
        m1=await c.ev("__vtMark()"); ts1=await toast()
        await c.ev("__vtSeek(9.0)"); await asyncio.sleep(0.3); await O()
        m2=await c.ev("__vtMark()"); ts2=await toast()
        sv=await c.ev("__vtSelectionView()")
        print("  按 I 後：", m1, " toast：", ts1)
        print("  按 O 後：", {k:m2[k] for k in ('in','out')}, " range：", m2["range"], " toast：", ts2)
        print("  選區 left/width（相對 24 秒）：",
              round(sv["left"],4), round(sv["width"],4), "期待", round(5/24,4), round(4/24,4))
        print("  是鍵盤範圍樣式：", sv["mark"], " 標籤：", sv["text"])
        print("  位置正確：", abs(sv["left"]-5/24)<0.01 and abs(sv["width"]-4/24)<0.01)
        await c.shot("Z-進出點選區")

        print("【Z3】只標單邊時，另一端跟著播放頭")
        await ESC(); await c.ev("__vtSeek(5.0)"); await asyncio.sleep(0.3); await I()
        w=[]
        for t in (7.0, 12.0, 18.0):
            await c.ev(f"__vtSeek({t})"); await asyncio.sleep(0.35)
            sv=await c.ev("__vtSelectionView()")
            w.append((t, round(sv["width"],4), sv["text"]))
        print("  播放頭 → 選區寬度：", w)
        print("  寬度跟著長大：", all(abs(x[1]-(x[0]-5)/24)<0.012 for x in w))

        print("【Z4】出點跑到進點前面 → 另一端清掉重來")
        await ESC(); await c.ev("__vtSeek(12.0)"); await asyncio.sleep(0.3); await I()
        await c.ev("__vtSeek(6.0)"); await asyncio.sleep(0.3); await O()
        m=await c.ev("__vtMark()")
        print("  在 12 標進點、回頭在 6 標出點 →", {k:m[k] for k in ('in','out')}, " range：", m["range"])
        print("  進點被清掉、只留出點：", m["in"] is None and abs(m["out"]-6.0)<1e-6)

        print("【Z5】⌫ 剪除標起來的範圍")
        await ESC(); await c.ev("__vtSeek(15.0)"); await asyncio.sleep(0.3); await I()
        await c.ev("__vtSeek(18.0)"); await asyncio.sleep(0.3); await O()
        before=await c.ev("__vtStats()")
        await BS()
        after=await c.ev("__vtStats()"); cuts=await c.ev("__vtPlan().cuts"); ts=await toast()
        print(f"  剪除前 cuts={before['cutTotal']} 剪後長={before['finalDuration']}")
        print(f"  剪除後 cuts={cuts} 剪除總長={after['cutTotal']} 剪後長={after['finalDuration']}　toast：{ts}")
        print("  範圍正確：", any(abs(a-15)<1e-6 and abs(b-18)<1e-6 for a,b in cuts))
        print("  不變式 24−3=21：", abs(after["duration"]-after["cutTotal"]-after["finalDuration"])<1e-6 and abs(after["finalDuration"]-21)<1e-6)
        print("  剪完選區收掉：", await c.ev("__vtSelectionView()"), "　mark 清空：", await c.ev("__vtMark()"))

        print("【Z6】⌘Z 復原鍵盤剪除")
        await UNDO(); await asyncio.sleep(0.3)
        st=await c.ev("__vtStats()")
        print("  ⌘Z 後 cuts=", await c.ev("__vtPlan().cuts"), " 剪後長=", st["finalDuration"])
        print("  完全還原：", st["cutTotal"]==before["cutTotal"] and st["finalDuration"]==before["finalDuration"])

        print("【Z7】沒標範圍就按 ⌫ → 給提示、不動資料")
        await blur(); await c.ev("__vtSeek(3.0)"); await asyncio.sleep(0.3)
        s0=await c.ev("__vtStats()")
        await BS()
        s1=await c.ev("__vtStats()"); ts=await toast()
        print("  toast：", ts)
        print("  資料零改動：", s0==s1, "　mark 仍空：", await c.ev("__vtMark()"))

        print("【Z8】Esc 取消標記")
        await c.ev("__vtSeek(4.0)"); await asyncio.sleep(0.3); await I()
        await c.ev("__vtSeek(8.0)"); await asyncio.sleep(0.3); await O()
        on=await c.ev("!!__vtSelectionView()")
        await ESC()
        print("  Esc 前選區在：", on, "　Esc 後：", await c.ev("__vtSelectionView()"),
              "　mark：", await c.ev("__vtMark()"))

        print("【Z9】焦點在字幕輸入框時，快捷鍵一律讓給打字")
        await c.ev("__vtStartEdit(4)"); await asyncio.sleep(0.4)
        eb0=await c.ev("__vtEditBox()")
        t_before=await now(); paused0=await c.ev("document.getElementById('vt-video').paused")
        n_before=len(await c.ev("__vtSubs()")); cuts0=await c.ev("__vtPlan().cuts")
        print("  焦點：", await c.ev("document.activeElement.tagName"), "　原句：", eb0["value"])
        await c.ev("(()=>{const e=document.activeElement;e.setSelectionRange(e.value.length,e.value.length)})()")
        await c.type(" ")  # 空白：應該被打進去，不是播放/暫停
        v1=(await c.ev("__vtEditBox()"))["value"]
        paused1=await c.ev("document.getElementById('vt-video').paused")
        await RIGHT(n=3); await LEFT()  # 方向鍵：移游標，不是移播放頭
        t_after=await now()
        await c.ev("(()=>{const e=document.querySelector('.vt-line-input:focus')||document.activeElement;e.setSelectionRange(e.value.length,e.value.length)})()")
        await BS()  # 句尾 Backspace：刪一個字，不是剪除
        v2=(await c.ev("__vtEditBox()"))["value"]
        await c.type("io"); await asyncio.sleep(0.2)  # I/O：打字，不是標進出點
        v3=(await c.ev("__vtEditBox()"))["value"]
        mk=await c.ev("__vtMark()")
        print(f"  空白鍵 → 值「{v1}」　影片 paused {paused0}→{paused1}（不該變）")
        print(f"  方向鍵 → 播放頭 {t_before}→{t_after}（不該變）")
        print(f"  句尾 ⌫ → 值「{v2}」　句數 {n_before}→{len(await c.ev('__vtSubs()'))}　cuts 不變：{cuts0==await c.ev('__vtPlan().cuts')}")
        print(f"  I/O → 值「{v3}」　mark 仍空：{mk['in'] is None and mk['out'] is None}")
        ok9=(v1==eb0["value"]+" " and paused1==paused0 and abs(t_after-t_before)<1e-6
             and v2==v1[:-1] and v3==v2+"io" and mk["in"] is None and mk["out"] is None)
        print("  守門全數成立：", ok9)
        await ESC(); await asyncio.sleep(0.3)

        print("【Z10】離開輸入框後快捷鍵恢復 + 回歸不變式")
        await blur(); await c.ev("__vtSeek(20.0)"); await asyncio.sleep(0.3); await I()
        await c.ev("__vtSeek(22.0)"); await asyncio.sleep(0.3); await O()
        await BS(); await asyncio.sleep(0.3)
        st=await c.ev("__vtStats()")
        print("  cuts=", await c.ev("__vtPlan().cuts"))
        print(f"  原長 {st['duration']} − 剪除 {st['cutTotal']} = 剪後 {st['finalDuration']}：",
              abs(st["duration"]-st["cutTotal"]-st["finalDuration"])<1e-6)
        print("  右欄句數：", len(await c.ev("__vtLines()")), "　標題卡：", len(await c.ev("__vtCards()")))
        await c.shot("Z-鍵盤剪除後")
        print("例外：", len(c.exc))
        for e in c.exc: print("   ", e[:400])
asyncio.run(main())
