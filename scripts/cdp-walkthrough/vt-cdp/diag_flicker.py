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
            if r[0]>=1 and r[1]>0: break
            await asyncio.sleep(0.5)
        print("環境 readyState/seekable：",r)
        if not (r[0]>=1 and r[1]>0): raise SystemExit("不可 seek，這輪不算數")

        print("【D1】字幕時間資料：句與句之間有沒有空隙")
        gaps=await c.ev("""(()=>{const s=__vtSubs?__vtSubs():__vt().subs;
          const rows=s.map(x=>[+x.start.toFixed(3),+x.end.toFixed(3)]);
          const g=[];for(let i=1;i<rows.length;i++){const d=+(rows[i][0]-rows[i-1][1]).toFixed(3);if(d>1e-6)g.push([rows[i-1][1],rows[i][0],d]);}
          return {n:rows.length,head:rows[0],tail:rows[rows.length-1],gaps:g}})()""")
        print("  句數",gaps["n"],"首句",gaps["head"],"末句",gaps["tail"])
        print("  空隙（前句尾→後句頭，秒）：")
        for a,b,d in gaps["gaps"]: print(f"    {a} → {b}　空 {d}s")
        print("  片頭 0→首句：", gaps["head"][0], "　末句→片尾：", round(24-gaps["tail"][1],3))

        print("【D2】升格兩張標題卡，然後全片播放逐幀取樣")
        await c.ev("__vtPromote(2)"); await asyncio.sleep(0.2)
        await c.ev("__vtPromote(5)"); await asyncio.sleep(0.2)
        await c.ev("__vtSeek(0)"); await asyncio.sleep(0.3)
        await c.ev("""(()=>{window.__samp=[];const rec=()=>{
          const v=document.getElementById('vt-video');
          const w=document.getElementById('vt-sub-preview');
          const el=document.getElementById('vt-sub-preview-text');
          const n=document.querySelectorAll('#vt-card-layer .vt-card-view').length;
          window.__samp.push([+v.currentTime.toFixed(3), w&&w.hidden?0:1, el?el.textContent:'', n]);
          if(!v.paused && window.__samp.length<4000) requestAnimationFrame(rec);};
          document.getElementById('vt-video').play().then(()=>requestAnimationFrame(rec));})()""")
        await asyncio.sleep(26)
        n=await c.ev("window.__samp.length")
        print("  取樣幀數：",n)
        # 只回傳「有變化」的幀，避免大量原文進主對話
        ch=await c.ev("""(()=>{const s=window.__samp;const out=[];let prev=null;
          for(const r of s){const k=r[1]+'|'+r[2]+'|'+r[3];if(k!==prev){out.push([r[0],r[1],r[2].slice(0,10),r[3]]);prev=k;}}
          return out})()""")
        print("  狀態變化次數：",len(ch))
        for t,vis,txt,nc in ch: print(f"   {t:>6}  疊層{'顯示' if vis else '隱藏'}  卡{nc}  「{txt}」")

        print("【D3】判定閃爍：同一段內容出現→消失→再出現，或停留 <0.5s")
        seg=[]
        for i,(t,vis,txt,nc) in enumerate(ch):
            dur = (ch[i+1][0]-t) if i+1<len(ch) else round(24-t,3)
            seg.append((t,round(dur,3),vis,txt,nc))
        short=[x for x in seg if x[1]<0.5]
        print("  停留不到 0.5 秒的狀態段：",len(short))
        for t,d,vis,txt,nc in short: print(f"   {t} 停留 {d}s  {'顯示' if vis else '隱藏'} 卡{nc} 「{txt}」")
        print("例外：",len(c.exc))
        for e in c.exc: print("   ",e[:200])
asyncio.run(main())
