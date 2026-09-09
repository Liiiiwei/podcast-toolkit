import asyncio, json, urllib.request, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])

async def main():
    pages=[t for t in hj("/json/list") if t["type"]=="page"]
    page=next((t for t in pages if "video-edit-prototype" in t["url"]), pages[-1])
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for m in ("Page.enable","Runtime.enable","Network.enable"): await c.send(m)
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        async def load():
            await c.send("Page.navigate",{"url":URL+"&n="+str(id(c))+str(len(c.exc))})
            for _ in range(30):
                await asyncio.sleep(0.4)
                if await c.ev("(typeof __vtSubs==='function')?__vtSubs().length:0"): return
        await load(); c.exc.clear()
        await load(); await asyncio.sleep(0.6); c.exc.clear()
        async def meta(k,vk,mod=1|4):  # meta+key（macOS ⌘）
            for t in ("rawKeyDown","keyUp"):
                await c.send("Input.dispatchKeyEvent",{"type":t,"key":k,"code":"Key"+k.upper(),
                    "windowsVirtualKeyCode":vk,"nativeVirtualKeyCode":vk,"modifiers":mod})

        print("【A】⌘Z 復原的是「剛剛那一步」嗎")
        await c.ev("__vtAddCut(1.0,3.0)"); await asyncio.sleep(0.3)
        n0=len(await c.ev("__vtSubs()")); cut0=len(await c.ev("__vt.cuts"))
        await c.ev("__vtStartEdit(2,4)"); await asyncio.sleep(0.4)
        await c.key("Enter",13); await asyncio.sleep(0.5)
        n1=len(await c.ev("__vtSubs()"))
        await c.ev("document.activeElement.blur()"); await asyncio.sleep(0.3)
        await meta("z",90); await asyncio.sleep(0.6)
        n2=len(await c.ev("__vtSubs()")); cut2=len(await c.ev("__vt.cuts"))
        print(f"  剪除1段後句數={n0} cuts={cut0} → 斷句後={n1} → ⌘Z 後 句數={n2} cuts={cut2}")
        print(f"  ✓ 復原了斷句：{n2==n0}　✓ 剪除沒被連坐：{cut2==cut0}")

        print("【E】播放頭高亮右欄目前句 + 自動捲")
        subs=await c.ev("__vtSubs()")
        tgt=min(9,len(subs)-1); mid=(subs[tgt]["start"]+subs[tgt]["end"])/2
        await c.ev(f"__vtSeek({mid})"); await asyncio.sleep(0.5)
        hi=await c.ev("Array.from(document.querySelectorAll('.vt-line.is-playing')).map(n=>+n.dataset.i)")
        vis=await c.ev("""(()=>{const r=document.querySelector('.vt-line.is-playing');if(!r)return null;
          const a=r.getBoundingClientRect(),b=document.getElementById('vt-line-list').getBoundingClientRect();
          return a.top>=b.top-1&&a.bottom<=b.bottom+1})()""")
        print(f"  播放頭在第{tgt+1}句({mid:.1f}s) → 高亮列 index={hi}（期待 [{tgt}]）　在視野內：{vis}")

        print("【D】點時間軸字幕塊會 seek")
        await c.ev("__vtSeek(0)"); await asyncio.sleep(0.4)
        pos=await c.ev("(()=>{const e=document.querySelectorAll('#vt-sub-track .vt-sub')[6].getBoundingClientRect();return{x:e.x+e.width/2,y:e.y+e.height/2}})()")
        await c.click(pos["x"],pos["y"]); await asyncio.sleep(0.6)
        ct=await c.ev("document.getElementById('vt-video').currentTime")
        fi=await c.ev("(()=>{const a=document.activeElement;return a&&a.closest('.vt-line')?+a.closest('.vt-line').dataset.i:null})()")
        print(f"  點第7塊 → currentTime={ct:.2f}（期待 {subs[6]['start']:.2f}）　右欄焦點 index={fi}（期待 6）")

        print("【J】同一句重複按「卡」")
        await c.ev("(()=>{const b=[...document.querySelectorAll('.vt-line')][1].querySelector('.vt-line-tool.is-card');b.click();b.click();b.click();})()")
        await asyncio.sleep(0.6)
        cards=await c.ev("__vtCards()")
        spans=["%.2f-%.2f"%(x["start"],x["end"]) for x in cards]
        print(f"  連按 3 次 → 卡數={len(cards)}（期待 1）{spans}")

        print("【P】播放頭在區間外時疊層不該還掛著")
        cid=cards[0]["id"]
        await c.ev(f"__vtSelectCard({cid})"); await asyncio.sleep(0.5)
        inview=await c.ev("!!document.querySelector('#vt-card-layer .vt-card-view')")
        ct2=await c.ev("document.getElementById('vt-video').currentTime")
        await c.ev("__vtSeek(20.0)"); await asyncio.sleep(0.6)
        outview=await c.ev("!!document.querySelector('#vt-card-layer .vt-card-view')")
        print(f"  卡區間 {cards[0]['start']:.2f}–{cards[0]['end']:.2f}；選取後播放頭自動落在 {ct2:.2f}s，疊層可見={inview}")
        print(f"  播放頭移到 20.0s（區間外）→ 疊層還在={outview}（期待 False）")

        print("【Q】沒選卡時點版型縮圖")
        await c.ev("(()=>{__vt.selectedCard=null;document.getElementById('vt-card-track').click();})()"); await asyncio.sleep(0.4)
        before=len(await c.ev("__vtCards()"))
        q=await c.ev("(()=>{const t=document.querySelector('#vt-tpl-grid .vt-tpl[data-tpl=\"quote\"]').getBoundingClientRect();return{x:t.x+t.width/2,y:t.y+t.height/2}})()")
        await c.click(q["x"],q["y"]); await asyncio.sleep(0.7)
        after=await c.ev("__vtCards()")
        print(f"  卡數 {before} → {len(after)}　新卡版型={after[-1]['tpl'] if after else None}（期待 quote）")

        print("【R】拖過位置的卡，換版型不該被洗掉")
        nid=after[-1]["id"]
        await c.ev(f"__vtSelectCard({nid})"); await asyncio.sleep(0.5)
        for _ in range(12):
            if await c.ev("!!document.querySelector('#vt-card-layer .vt-card-view')"): break
            await asyncio.sleep(0.25)
        g=await c.ev("(()=>{const e=document.querySelector('#vt-card-layer .vt-card-view');if(!e)return null;const r=e.getBoundingClientRect();const l=document.getElementById('vt-card-layer').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2,lx:l.x,ly:l.y,lw:l.width,lh:l.height}})()")
        await c.drag(g["x"],g["y"],g["lx"]+g["lw"]*0.3,g["ly"]+g["lh"]*0.25,steps=10); await asyncio.sleep(0.5)
        moved=[x for x in await c.ev("__vtCards()") if x["id"]==nid][0]
        b2=await c.ev("(()=>{const t=document.querySelector('#vt-tpl-grid .vt-tpl[data-tpl=\"big\"]').getBoundingClientRect();return{x:t.x+t.width/2,y:t.y+t.height/2}})()")
        await c.click(b2["x"],b2["y"]); await asyncio.sleep(0.5)
        aft=[x for x in await c.ev("__vtCards()") if x["id"]==nid][0]
        print(f"  拖到 x={moved['x']:.3f} y={moved['y']:.3f} → 換成大字報後 x={aft['x']:.3f} y={aft['y']:.3f} tpl={aft['tpl']}　位置保住：{abs(aft['x']-moved['x'])<1e-6 and abs(aft['y']-moved['y'])<1e-6}")
        rp=await c.ev("(()=>{const b=document.getElementById('ct-reset-pos').getBoundingClientRect();return{x:b.x+b.width/2,y:b.y+b.height/2}})()")
        await c.click(rp["x"],rp["y"]); await asyncio.sleep(0.4)
        rst=[x for x in await c.ev("__vtCards()") if x["id"]==nid][0]
        print(f"  按「回版型預設位」→ x={rst['x']} y={rst['y']}（big 預設 0.5/0.5）")

        print("【H】開始改字時標題卡面板要收掉")
        await c.ev("__vtStartEdit(3,2)"); await asyncio.sleep(0.5)
        print(f"  inspector hidden={await c.ev('document.getElementById(\"vt-card-inspector\").hidden')}（期待 True）　selectedCard={await c.ev('__vt.selectedCard')}")

        print("【回歸】斷句／合併／不變式／右欄")
        s0=await c.ev("__vtSubs()")
        await c.ev("__vtStartEdit(4,0)"); await asyncio.sleep(0.4)
        for _ in range(6): await c.key("ArrowRight",39)
        await c.key("Enter",13); await asyncio.sleep(0.5)
        s1=await c.ev("__vtSubs()")
        env = abs(s1[4]["start"]-s0[4]["start"])<1e-9 and s1[5]["end"]<=s0[4]["end"]+1e-9
        await c.key("Home",36); await c.key("Backspace",8); await asyncio.sleep(0.5)
        s2=await c.ev("__vtSubs()")
        st=await c.ev("__vtStats()")
        print(f"  斷句 {len(s0)}→{len(s1)}（封套沒超出：{env}）　合併回 {len(s2)} 句")
        print(f"    第5句 原：{s0[4]['text']}  合併後：{s2[4]['text']}  一致：{len(s2)==len(s0) and s2[4]['text']==s0[4]['text']}")
        print(f"  不變式 {st['duration']}−{st['cutTotal']}={st['finalDuration']}：{abs(st['duration']-st['cutTotal']-st['finalDuration'])<1e-6}")
        print(f"  右欄 {await c.ev('document.getElementById(\"vt-line-count\").textContent')}　列數={await c.ev('document.querySelectorAll(\".vt-line\").length')}")
        await c.ev("document.activeElement.blur()"); await asyncio.sleep(0.3)
        await c.ev("__vtSeek(5.6)"); await asyncio.sleep(0.6)
        await c.shot("I1-播放高亮")
        print("例外：",len(c.exc))
        for e in c.exc: print("  ",e[:400])
asyncio.run(main())
