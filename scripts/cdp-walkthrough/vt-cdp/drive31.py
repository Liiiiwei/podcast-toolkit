import asyncio, json
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])

async def drag(c, x, y, dx):
    await c.send("Input.dispatchMouseEvent",{"type":"mousePressed","x":x,"y":y,"button":"left","clickCount":1})
    for k in (0.3,0.7,1.0):
        await c.send("Input.dispatchMouseEvent",{"type":"mouseMoved","x":x+dx*k,"y":y,"button":"left"})
        await asyncio.sleep(0.08)
    await c.send("Input.dispatchMouseEvent",{"type":"mouseReleased","x":x+dx,"y":y,"button":"left","clickCount":1})
    await asyncio.sleep(0.45)

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))}); await asyncio.sleep(3.5)

        for _ in range(60):
            r=await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState,v.seekable.length]})()")
            if r and r[0]>=1 and r[1]>0: break
            await asyncio.sleep(0.5)
        else:
            raise SystemExit("影片不可 seek，這輪測到的不算數")
        print("video readyState/seekable：",r)

        VIEW="""(()=>{const w=document.getElementById('vt-sub-preview');
          return {hidden:w.hidden, text:document.getElementById('vt-sub-preview-text').textContent,
                  cards:document.querySelectorAll('#vt-card-layer .vt-card-view').length}})()"""
        def ROW(i): return f"""(()=>{{const r=document.querySelector('.vt-line[data-i="{i}"]');
          const b=Array.from(r.querySelectorAll('.vt-line-tool')).find(x=>x.getAttribute('aria-label')==='卡');
          return {{carded:r.classList.contains('is-carded'), btnOn:b.classList.contains('is-on')}}}})()"""
        CARD="(()=>{const c=__vt.cards[0];return c?{start:c.start,end:c.end,src:c.src}:null})()"

        i=3
        sub=await c.ev(f"(()=>{{const s=__vt.subs[{i}];return [s.start,s.end,s.text]}})()")
        mid=(sub[0]+sub[1])/2
        print(f"【H0】版本指紋：升格後的卡要記得娘家（沒有＝來源沒同步）")
        await c.ev(f"__vtPromote({i})"); await asyncio.sleep(0.6)
        c0=await c.ev(CARD)
        print("  卡：", c0)
        if not c0 or not c0.get("src"):
            raise SystemExit("卡沒有 src 血緣欄位 —— 來源未同步，這輪不算數")
        print(f"  受測句 第{i+1}句「{sub[2]}」 {sub[0]}–{sub[1]}，中點 {mid:.2f}s")

        print("【H1】基準：剛升格時字幕讓位")
        await c.ev(f"__vtSeek({mid})"); await asyncio.sleep(0.5)
        v1=await c.ev(VIEW); r1=await c.ev(ROW(i))
        print("  ", v1, r1)
        ok1 = v1["hidden"] is True and v1["cards"]==1 and r1["carded"] is True
        print("  H1 通過：", ok1)

        print("【H2】把卡拖長（右把手 +60px）→ 時間對不上原句了，但不該重複燒")
        hr=await c.ev("""(()=>{const h=document.querySelector('#vt-card-track .vt-card-h.is-r');
          const r=h.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()""")
        await drag(c, hr["x"], hr["y"], 60)
        c2=await c.ev(CARD)
        await c.ev(f"__vtSeek({mid})"); await asyncio.sleep(0.5)
        v2=await c.ev(VIEW); r2=await c.ev(ROW(i))
        print(f"  出點 {c0['end']} → {c2['end']}（血緣仍指向 {c2['src']}）")
        print("  ", v2, r2)
        n_before=len(await c.ev("__vtCards()"))
        await c.ev(f"__vtPromote({i})"); await asyncio.sleep(0.5)
        n_after=len(await c.ev("__vtCards()"))
        ok2 = (c2["end"]>c0["end"]+0.3 and v2["hidden"] is True and v2["text"]==""
               and v2["cards"]==1 and r2["carded"] is True and n_before==1 and n_after==1)
        print(f"  字幕仍讓位（不重複燒）：{v2['hidden'] is True}　右欄仍標已升格：{r2['carded']}"
              f"　再按一次「卡」不疊卡：{n_before}→{n_after}")
        print("  H2 通過：", ok2)
        await c.shot("H-拖長後不重複燒")

        print("【H3】把卡整塊拖到別的時間 → 這一刻沒卡在演，字幕要自己回來")
        blk=await c.ev("""(()=>{const b=document.querySelector('#vt-card-track .vt-card');
          const r=b.getBoundingClientRect();return {x:r.x+25,y:r.y+r.height/2,w:r.width}})()""")
        await drag(c, blk["x"], blk["y"], 220)
        c3=await c.ev(CARD)
        await c.ev(f"__vtSeek({mid})"); await asyncio.sleep(0.5)
        v3=await c.ev(VIEW); r3=await c.ev(ROW(i))
        print(f"  卡移到 {round(c3['start'],2)}–{round(c3['end'],2)}；播放頭回原句中點 {mid:.2f}s")
        print("  ", v3, r3)
        newmid=(c3["start"]+c3["end"])/2
        await c.ev(f"__vtSeek({newmid})"); await asyncio.sleep(0.5)
        v3b=await c.ev(VIEW)
        print(f"  播放頭移到卡的新位置 {newmid:.2f}s → ", v3b)
        ok3 = (c3["start"]>sub[0]+0.5 and v3["hidden"] is False and v3["text"]==sub[2]
               and v3["cards"]==0 and r3["carded"] is True and v3b["cards"]==1)
        print(f"  原句字幕回來了：{v3['text']==sub[2] and v3['hidden'] is False}"
              f"　右欄血緣還在：{r3['carded']}　卡在新位置演得出來：{v3b['cards']==1}")
        print("  H3 通過：", ok3)
        await c.shot("H-拖走後字幕自己回來")

        print("【H4】⌘Z 逐步復原（拖走 → 拖長 → 原始封套）")
        seq=[]
        for _ in range(2):
            for t in ("rawKeyDown","keyUp"):
                await c.send("Input.dispatchKeyEvent",{"type":t,"key":"z","code":"KeyZ",
                  "windowsVirtualKeyCode":90,"nativeVirtualKeyCode":90,"modifiers":4})
            await asyncio.sleep(0.5)
            s=await c.ev(CARD)
            seq.append(None if not s else (round(s["start"],2),round(s["end"],2)))
        print("  ⌘Z 三次：", seq)
        await c.ev(f"__vtSeek({mid})"); await asyncio.sleep(0.5)
        v4=await c.ev(VIEW)
        ok4 = seq[-1]==(round(sub[0],2),round(sub[1],2)) and v4["hidden"] is True
        print(f"  回到原始封套 {seq[-1]}（期待 {(round(sub[0],2),round(sub[1],2))}）　字幕讓位：{v4['hidden']}")
        print("  H4 通過：", ok4)

        print("【H5】round-trip：血緣寫進指令（fromSubtitle），src 本身仍是 UI 內部狀態")
        p=await c.ev("__vtPlan()")
        print("  指令裡的卡：", [(x["start"],x["end"]) for x in p["cards"]])
        has_src = any("src" in x for x in p["cards"])
        await c.ev("__vtApplyPlan(%s)"%json.dumps(json.dumps(p))); await asyncio.sleep(0.6)
        c5=await c.ev(CARD)
        await c.ev(f"__vtSeek({mid})"); await asyncio.sleep(0.5)
        v5=await c.ev(VIEW); r5=await c.ev(ROW(i))
        print("  指令內含 src 欄位：", has_src, "（應為 False，那是 UI 內部狀態）")
        print("  匯入後的卡：", c5)
        print("  ", v5, r5)
        ok5 = (has_src is False and c5["src"] is not None
               and v5["hidden"] is True and r5["carded"] is True)
        print("  H5 通過（時間對齊的卡，匯入後血緣重建成功）：", ok5)

        print("【H5b】被拖過長度的卡，匯出再匯入仍要認得娘家（曾經的已知限制，已修）")
        hr=await c.ev("""(()=>{const h=document.querySelector('#vt-card-track .vt-card-h.is-r');
          const r=h.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()""")
        await drag(c, hr["x"], hr["y"], 60)
        p2=await c.ev("__vtPlan()")
        await c.ev("__vtApplyPlan(%s)"%json.dumps(json.dumps(p2))); await asyncio.sleep(0.6)
        c6=await c.ev(CARD)
        await c.ev(f"__vtSeek({mid})"); await asyncio.sleep(0.5)
        v6=await c.ev(VIEW); r6=await c.ev(ROW(i))
        print("  匯入後的卡：", c6)
        print("  ", v6, r6)
        ok5b = (c6["src"] is not None and v6["hidden"] is True and r6["carded"] is True)
        print("  血緣重建：", c6["src"] is not None, "　字幕仍讓位：", v6["hidden"],
              "　右欄仍標已升格：", r6["carded"])
        print("  H5b 通過：", ok5b)
        for _ in range(2):
            for t in ("rawKeyDown","keyUp"):
                await c.send("Input.dispatchKeyEvent",{"type":t,"key":"z","code":"KeyZ",
                  "windowsVirtualKeyCode":90,"nativeVirtualKeyCode":90,"modifiers":4})
            await asyncio.sleep(0.5)

        print("【H6】回歸不變式")
        await c.ev("__vtAddCut(4.0,6.0)"); await asyncio.sleep(0.4)
        st=await c.ev("__vtStats()")
        print(f"  {st['duration']}−{st['cutTotal']}={st['finalDuration']}　成立：",
              abs(st["duration"]-st["cutTotal"]-st["finalDuration"])<1e-6)
        print("  句數：", len(await c.ev("__vtSubs()")), "　卡數：", len(await c.ev("__vtCards()")))
        print("全部通過：", ok1 and ok2 and ok3 and ok4 and ok5 and ok5b)
        print("例外：",len(c.exc))
        for e in c.exc: print("   ",e[:300])
asyncio.run(main())
