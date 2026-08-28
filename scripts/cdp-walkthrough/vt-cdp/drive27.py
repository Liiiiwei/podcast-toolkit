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

        # 媒體可 seek 才算環境正常（python -m http.server 不支援 Range 會靜默失效）
        for _ in range(20):
            r=await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState,v.seekable.length]})()")
            if r[0]>=1 and r[1]>0: break
            await asyncio.sleep(0.4)
        print("video readyState/seekable：",r)

        print("【Y0】版本指紋")
        print("  __vtPlan：", await c.ev("typeof window.__vtPlan"),
              "　按鈕：", await c.ev("!!document.getElementById('vt-plan-open')"),
              "　面板初始隱藏：", await c.ev("document.getElementById('vt-plan').hidden"))

        print("【Y1】乾淨狀態：keep＝全片")
        p=await c.ev("__vtPlan()")
        print(f"  version={p['version']} source={p['source']} duration={p['duration']} final={p['finalDuration']}")
        print("  cuts=",p["cuts"],"　keep=",p["keep"])
        print("  keep 就是整支：", p["keep"]==[[0,p["duration"]]], "　剪後＝原長：", p["finalDuration"]==p["duration"])
        print("  無 dropped：", not any(x["dropped"] for x in p["subtitles"]))

        print("【Y2】剪除後 keep 與 cuts 互補")
        await c.ev("__vtAddCut(4.0,7.0)"); await c.ev("__vtAddCut(12.0,13.5)"); await asyncio.sleep(0.3)
        await c.ev("__vtPromote(1)"); await asyncio.sleep(0.3)
        p=await c.ev("__vtPlan()")
        print("  cuts=",p["cuts"],"\n  keep=",p["keep"])
        cut_total=sum(e-s for s,e in p["cuts"]); keep_total=sum(e-s for s,e in p["keep"])
        print(f"  Σ剪除={cut_total} Σ保留={keep_total} 原長={p['duration']}")
        print("  互補（Σ剪除+Σ保留＝原長）：", abs(cut_total+keep_total-p["duration"])<1e-6)
        print("  finalDuration＝Σ保留：", abs(p["finalDuration"]-keep_total)<1e-6)
        st=await c.ev("__vtStats()")
        print("  與頂部統計一致：", abs(st["finalDuration"]-p["finalDuration"])<1e-6)

        print("【Y3】剪後時間映射 outStart/outEnd")
        # 剪除 [4,7] 與 [12,13.5]：原 10s → 剪後 7s；原 20s → 剪後 20-3-1.5=15.5s
        probe=await c.ev("""(()=>{const p=__vtPlan();
          return {a:p.subtitles.map(s=>[s.start,s.outStart,s.dropped])}})()""")
        rows=probe["a"]
        for s,o,d in rows[:12]:
            exp = s - (3 if s>=7 else (s-4 if 4<s<7 else 0)) - (1.5 if s>=13.5 else (s-12 if 12<s<13.5 else 0))
            ok = abs(o-round(exp,3))<0.01
            print(f"   原 {s:>6} → 剪後 {o:>6}（期待 {round(exp,3):>6}）{'✓' if ok else '✗'}{' dropped' if d else ''}")

        print("【Y4】整段落在剪除區的句子要標 dropped")
        await c.ev("__vtAddCut(0.0,3.5)"); await asyncio.sleep(0.3)
        p=await c.ev("__vtPlan()")
        drop=[(round(s['start'],2),round(s['end'],2),s['text'][:12]) for s in p["subtitles"] if s["dropped"]]
        part=[(round(s['start'],2),round(s['end'],2)) for s in p["subtitles"] if not s["dropped"] and s["start"]<3.5]
        print("  被標 dropped 的句：",drop)
        print("  與剪除段部分重疊但保留的：",part)
        print("  dropped 者確實整段落在剪除內：",
              all(any(cs<=a+1e-6 and b<=ce+1e-6 for cs,ce in p["cuts"]) for a,b,_ in drop))

        print("【Y5】真 UI：點「輸出剪輯指令」")
        b=await c.ev("(()=>{const r=document.getElementById('vt-plan-open').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(b["x"],b["y"]); await asyncio.sleep(0.5)
        d=await c.ev("__vtPlanDialog()")
        print("  面板開啟：",d["open"],"　JSON 字數：",d["len"])
        print("  摘要：",d["summary"])
        parsed=await c.ev("(()=>{try{const o=JSON.parse(document.getElementById('vt-plan-json').value);return{ok:true,keys:Object.keys(o)}}catch(e){return{ok:false,err:e.message}}})()")
        print("  textarea 內容是合法 JSON：",parsed["ok"],"　欄位：",parsed.get("keys"))
        await c.shot("Y-剪輯指令面板")

        print("【Y6】round-trip：匯出 → 洗掉 → 套用 → 逐欄比對")
        snap=await c.ev("(()=>{const p=__vtPlan();return {json:JSON.stringify(p),cuts:p.cuts,subs:p.subtitles.map(s=>[s.start,s.end,s.text]),cards:p.cards.map(c=>[c.start,c.end,c.tpl,c.text,c.scale,c.x,c.y]),style:p.style}})()")
        # 洗掉：換一組完全不同的狀態
        await c.ev("(()=>{__vt.cuts.length=0;__vt.cards.length=0;__vtAddCut(1,2);__vtSetStyle({font_size:30,margin_v:10});})()")
        await c.ev("(()=>{const s=__vt.subs;s.splice(3);s[0].text='被洗掉的字';})()")
        await c.ev("(()=>{try{render()}catch(e){}})()"); await asyncio.sleep(0.3)
        washed=await c.ev("(()=>{const p=__vtPlan();return {cuts:p.cuts,nsub:p.subtitles.length,ncard:p.cards.length,size:p.style.font_size}})()")
        print("  洗掉後：cuts=",washed["cuts"],"句數=",washed["nsub"],"卡數=",washed["ncard"],"字級=",washed["size"])
        # 透過真 UI 套用：把 JSON 貼回 textarea 再按鈕
        await c.ev("(()=>{document.getElementById('vt-plan').hidden=false;})()")
        await c.send("Runtime.evaluate",{"expression":"document.getElementById('vt-plan-json').value=%s"%json.dumps(snap["json"])})
        ab=await c.ev("(()=>{const r=document.getElementById('vt-plan-apply').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(ab["x"],ab["y"]); await asyncio.sleep(0.6)
        back=await c.ev("(()=>{const p=__vtPlan();return {cuts:p.cuts,subs:p.subtitles.map(s=>[s.start,s.end,s.text]),cards:p.cards.map(c=>[c.start,c.end,c.tpl,c.text,c.scale,c.x,c.y]),style:p.style,open:!document.getElementById('vt-plan').hidden}})()")
        print("  套用後面板自動關閉：", not back["open"])
        for k in ("cuts","subs","cards"):
            print(f"  {k} 還原一致：", back[k]==snap[k], f"（{len(snap[k])} 項）")
        print("  style 還原一致：", back["style"]==snap["style"], "字級=",back["style"]["font_size"])
        print("  樣式面板控制項也回填：", await c.ev("document.getElementById('st-size').value"))

        print("【Y7】格式錯誤要看得見，且不得半套進去")
        before=await c.ev("(()=>{const p=__vtPlan();return {c:p.cuts.length,s:p.subtitles.length,k:p.cards.length}})()")
        for label,payload in [("版本不合",'{"version":9,"cuts":[],"subtitles":[],"cards":[]}'),
                              ("cuts 形狀錯",'{"version":1,"cuts":[[3]],"subtitles":[],"cards":[]}'),
                              ("缺 cards",'{"version":1,"cuts":[],"subtitles":[]}'),
                              ("不是 JSON",'{壞掉的')]:
            await c.send("Runtime.evaluate",{"expression":"document.getElementById('vt-plan').hidden=false;document.getElementById('vt-plan-json').value=%s"%json.dumps(payload)})
            await c.click(ab["x"],ab["y"]); await asyncio.sleep(0.35)
            d=await c.ev("__vtPlanDialog()")
            print(f"   {label} → 訊息「{d['msg']}」　面板仍開著：{d['open']}")
        after=await c.ev("(()=>{const p=__vtPlan();return {c:p.cuts.length,s:p.subtitles.length,k:p.cards.length}})()")
        print("  四次失敗後狀態零改動：", before==after, before, after)
        await c.shot("Y-套用失敗訊息")

        print("【Y8】⌘Z 復原匯入（含樣式）")
        await c.ev("__vtSetStyle({font_size:88})"); await asyncio.sleep(0.2)
        pre=await c.ev("(()=>{const p=__vtPlan();return{n:p.cuts.length,size:p.style.font_size,nsub:p.subtitles.length}})()")
        tiny=await c.ev("""(()=>{const p=__vtPlan();p.cuts=[[1,2]];p.subtitles=p.subtitles.slice(0,2);p.cards=[];p.style.font_size=20;return JSON.stringify(p)})()""")
        r=await c.ev("__vtApplyPlan(%s)"%json.dumps(tiny)); await asyncio.sleep(0.3)
        mid=await c.ev("(()=>{const p=__vtPlan();return{n:p.cuts.length,size:p.style.font_size,nsub:p.subtitles.length}})()")
        for t in ("rawKeyDown","keyUp"):
            await c.send("Input.dispatchKeyEvent",{"type":t,"key":"z","code":"KeyZ",
              "windowsVirtualKeyCode":90,"nativeVirtualKeyCode":90,"modifiers":4})
        await asyncio.sleep(0.5)
        post=await c.ev("(()=>{const p=__vtPlan();return{n:p.cuts.length,size:p.style.font_size,nsub:p.subtitles.length}})()")
        print("  匯入前",pre,"→ 匯入後",mid,"→ ⌘Z",post)
        print("  真的有變：", mid!=pre, "　一次還原（含樣式）：", post==pre)

        print("【Y9】回歸不變式 + 面板關閉")
        cb=await c.ev("(()=>{const r=document.getElementById('vt-plan-close').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.ev("(()=>{document.getElementById('vt-plan').hidden=false})()")
        await c.click(cb["x"],cb["y"]); await asyncio.sleep(0.3)
        print("  按關閉後 hidden：", await c.ev("document.getElementById('vt-plan').hidden"))
        st=await c.ev("__vtStats()"); p=await c.ev("__vtPlan()")
        print(f"  {st['duration']}−{st['cutTotal']}={st['finalDuration']}　不變式：",
              abs(st["duration"]-st["cutTotal"]-st["finalDuration"])<1e-6,
              "　plan 一致：", abs(p["finalDuration"]-st["finalDuration"])<1e-6)
        print("例外：",len(c.exc))
        for e in c.exc: print("   ",e[:300])
asyncio.run(main())
