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

        i=3
        sub=await c.ev(f"(()=>{{const s=__vt.subs[{i}];return [s.start,s.end,s.text]}})()")
        mid=(sub[0]+sub[1])/2
        print(f"【G0】受測句 第{i+1}句「{sub[2]}」 {sub[0]}–{sub[1]}，中點 {mid:.2f}s")
        print("  影片是暫停的：", await c.ev("document.getElementById('vt-video').paused"))

        print("【G1】暫停時刪卡：字要回來、右欄標記要清")
        await c.ev(f"__vtSeek({mid})"); await asyncio.sleep(0.5)
        v0=await c.ev(VIEW); print("  升格前：", v0)
        await c.ev(f"__vtPromote({i})"); await asyncio.sleep(0.6)
        await c.ev(f"__vtSeek({mid})"); await asyncio.sleep(0.5)
        v1=await c.ev(VIEW); r1=await c.ev(ROW(i))
        print("  升格後：", v1, r1)
        d=await c.ev("""(()=>{const b=document.querySelector('#vt-card-track .vt-card-del');
          if(!b) return null; const r=b.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}})()""")
        await c.click(d["x"],d["y"]); await asyncio.sleep(0.6)
        v2=await c.ev(VIEW); r2=await c.ev(ROW(i))
        print("  刪卡後：", v2, r2)
        print("  卡數歸零：", len(await c.ev("__vtCards()"))==0)
        ok1 = (v2["hidden"] is False and v2["text"]==sub[2] and v2["cards"]==0
               and r2["carded"] is False and r2["btnOn"] is False)
        print("  字幕回到原句：", v2["text"]==sub[2] and v2["hidden"] is False,
              "　右欄標記已清：", r2["carded"] is False and r2["btnOn"] is False)
        print("  G1 通過：", ok1)
        await c.shot("G-暫停刪卡後字幕回來")

        print("【G2】播放頭落在空隙時刪卡：不該憑空冒出字")
        gap=await c.ev("""(()=>{const s=__vt.subs;for(let k=0;k<s.length-1;k++){
          if(s[k+1].start-s[k].end>0.25) return (s[k].end+s[k+1].start)/2}return null})()""")
        await c.ev(f"__vtPromote({i})"); await asyncio.sleep(0.5)
        await c.ev(f"__vtSeek({gap})"); await asyncio.sleep(0.5)
        before=await c.ev(VIEW)
        d=await c.ev("""(()=>{const b=document.querySelector('#vt-card-track .vt-card-del');
          const r=b.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}})()""")
        await c.click(d["x"],d["y"]); await asyncio.sleep(0.6)
        after=await c.ev(VIEW)
        print(f"  播放頭 {gap:.2f}s（空隙）　刪卡前 {before} → 刪卡後 {after}")
        ok2 = after["hidden"] is True and after["text"]==""
        print("  G2 通過（仍留白）：", ok2)

        print("【G3】⌘Z 復原刪卡：卡回來、字幕再度讓位")
        await c.ev(f"__vtPromote({i})"); await asyncio.sleep(0.5)
        await c.ev(f"__vtSeek({mid})"); await asyncio.sleep(0.4)
        d=await c.ev("""(()=>{const b=document.querySelector('#vt-card-track .vt-card-del');
          const r=b.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}})()""")
        await c.click(d["x"],d["y"]); await asyncio.sleep(0.5)
        mid3=await c.ev(VIEW)
        for t in ("rawKeyDown","keyUp"):
            await c.send("Input.dispatchKeyEvent",{"type":t,"key":"z","code":"KeyZ",
              "windowsVirtualKeyCode":90,"nativeVirtualKeyCode":90,"modifiers":4})
        await asyncio.sleep(0.6)
        await c.ev(f"__vtSeek({mid})"); await asyncio.sleep(0.4)
        post=await c.ev(VIEW); rp=await c.ev(ROW(i))
        print("  刪卡後", mid3, "→ ⌘Z", post, rp)
        ok3 = len(await c.ev("__vtCards()"))==1 and post["hidden"] is True and rp["carded"] is True
        print("  G3 通過：", ok3)

        print("【G5】右把手讓位後，改出點還拖得動嗎")
        await c.ev("__vtPromote(3)"); await asyncio.sleep(0.6)
        hit=await c.ev("""(()=>{const blk=document.querySelector('#vt-card-track .vt-card');
          const b=blk.querySelector('.vt-card-del'), h=blk.querySelector('.vt-card-h.is-r');
          const br=b.getBoundingClientRect(), hr=h.getBoundingClientRect();
          const top=(r)=>{const e=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return e?e.className:null};
          return {刪鈕最上層:top(br), 右把手最上層:top(hr), 兩者不重疊:br.x>=hr.x+hr.width-0.5,
                  把手中心:[hr.x+hr.width/2, hr.y+hr.height/2]}})()""")
        print("  ", {k:v for k,v in hit.items() if k!="把手中心"})
        e0=(await c.ev("__vtCards()"))[0]["end"]
        hx,hy=hit["把手中心"]
        await c.send("Input.dispatchMouseEvent",{"type":"mousePressed","x":hx,"y":hy,"button":"left","clickCount":1})
        await c.send("Input.dispatchMouseEvent",{"type":"mouseMoved","x":hx+60,"y":hy,"button":"left"})
        await asyncio.sleep(0.3)
        await c.send("Input.dispatchMouseEvent",{"type":"mouseReleased","x":hx+60,"y":hy,"button":"left","clickCount":1})
        await asyncio.sleep(0.4)
        e1=(await c.ev("__vtCards()"))[0]["end"]
        ok5 = hit["刪鈕最上層"]=="vt-card-del" and "vt-card-h" in (hit["右把手最上層"] or "") and e1>e0+0.3
        print(f"  拖右把手 +60px：出點 {e0} → {e1}　G5 通過：", ok5)
        await c.shot("G-刪鈕與把手不再打架")

        print("【G4】回歸不變式")
        await c.ev("__vtAddCut(4.0,6.0)"); await asyncio.sleep(0.4)
        st=await c.ev("__vtStats()")
        print(f"  {st['duration']}−{st['cutTotal']}={st['finalDuration']}　成立：",
              abs(st["duration"]-st["cutTotal"]-st["finalDuration"])<1e-6)
        print("  句數：", len(await c.ev("__vtSubs()")), "　卡數：", len(await c.ev("__vtCards()")))
        print("全部通過：", ok1 and ok2 and ok3 and ok5)
        print("例外：",len(c.exc))
        for e in c.exc: print("   ",e[:300])
asyncio.run(main())
