import asyncio, json
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])

# 頁面內的逐幀取樣器：播完整片，每幀記下 (時間, 疊層可見, 文字, 卡數)
SAMPLER = """(()=>{window.__samp=[];const rec=()=>{
  const v=document.getElementById('vt-video');
  const w=document.getElementById('vt-sub-preview');
  const el=document.getElementById('vt-sub-preview-text');
  const n=document.querySelectorAll('#vt-card-layer .vt-card-view').length;
  window.__samp.push([+v.currentTime.toFixed(3), w&&w.hidden?0:1, el?el.textContent:'', n]);
  if(!v.paused && window.__samp.length<4000) requestAnimationFrame(rec);};
  document.getElementById('vt-video').play().then(()=>requestAnimationFrame(rec));})()"""

# 每一幀都拿「當下時間該有的字」去對，違規才回報
AUDIT = """(()=>{const subs=__vt.subs;const cards=__vtCards();
  const cardAt=(a,b)=>cards.some(c=>Math.abs(c.start-a)<1e-6&&Math.abs(c.end-b)<1e-6);
  const wantAt=(tt)=>{const s=subs.find(x=>tt>=x.start&&tt<x.end);return s?(cardAt(s.start,s.end)?'':s.text):'';};
  const bad=[];let ghost=0,edge=0,strict=0;
  for(const [t,vis,txt,nc] of window.__samp){
    const shown=vis?txt:'';const want=wantAt(t);
    if(shown===want) continue;
    strict++;
    // rAF 取樣不是原子的：DOM 上的字是這一幀稍早用「上一幀的 currentTime」算出來的，
    // 所以句子邊界前後那一幀本來就會對不上。容差一幀（0.05s）內視為過渡，不算違規。
    if(shown===wantAt(t-0.05)||shown===wantAt(t+0.05)){edge++;continue;}
    bad.push([t,shown.slice(0,12),want.slice(0,12)]);
    if(!subs.find(x=>t>=x.start&&t<x.end)&&shown) ghost++;
  }
  return {n:window.__samp.length,bad:bad.length,ghost:ghost,edge:edge,strict:strict,head:bad.slice(0,6)}})()"""

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
        print("video readyState/seekable：",r)
        if not (r[0]>=1 and r[1]>0): raise SystemExit("影片不可 seek，這輪測到的不算數")

        print("【F0】版本指紋")
        print("  樣式摺疊區有 id：", await c.ev("!!document.getElementById('vt-adv')"),
              "　預設收合：", await c.ev("!document.getElementById('vt-adv').open"))

        print("【F1】播放全片：每一幀的字都要等於「那一刻該有的字」")
        await c.ev("__vtPromote(2)"); await asyncio.sleep(0.2)
        await c.ev("__vtPromote(5)"); await asyncio.sleep(0.2)
        await c.ev("__vtSeek(0)"); await asyncio.sleep(0.3)
        await c.ev(SAMPLER); await asyncio.sleep(26)
        a=await c.ev(AUDIT)
        print(f"  取樣 {a['n']} 幀　嚴格不符 {a['strict']} 幀（邊界過渡 {a['edge']} 幀）→ 真違規 {a['bad']} 幀　其中「空隙卻有字」 {a['ghost']} 幀")
        for t,shown,want in a["head"]: print(f"    {t} 顯示「{shown}」期待「{want}」")
        print("  播放中零幽靈字幕：", a["ghost"]==0, "　逐幀全對：", a["bad"]==0)
        ch=await c.ev("""(()=>{const s=window.__samp;const o=[];let p=null;
          for(const r of s){const k=r[1]+'|'+r[2];if(k!==p){o.push([r[0],r[1],r[2].slice(0,8)]);p=k;}}return o})()""")
        first=await c.ev("__vt.subs[0].text")
        ghost_seg=[x for x in ch if x[1] and x[2] and first.startswith(x[2]) and x[0]>3]
        print(f"  狀態變化 {len(ch)} 次（修正前為 21 次，含 10 次閃第一句）")
        print("  第一句在片頭之後又冒出來的次數：", len(ghost_seg), "（期待 0）")

        print("【F2】突變測試：示範句那條分支還活著嗎")
        await c.ev("(()=>{document.getElementById('vt-video').pause()})()"); await asyncio.sleep(0.3)
        await c.ev("__vtSeek(10.5)"); await asyncio.sleep(0.4)  # 10.15–11.05 是空隙
        off=await c.ev("(()=>{const w=document.getElementById('vt-sub-preview');return {hidden:w.hidden,txt:document.getElementById('vt-sub-preview-text').textContent}})()")
        print("  暫停在空隙 10.5s、面板收合 →", off)
        await c.ev("(()=>{document.getElementById('vt-adv').open=true})()"); await asyncio.sleep(0.4)
        on=await c.ev("(()=>{const w=document.getElementById('vt-sub-preview');return {hidden:w.hidden,txt:document.getElementById('vt-sub-preview-text').textContent}})()")
        print("  展開樣式面板 →", on["hidden"], f"「{on['txt'][:14]}」")
        print("  收合＝留白：", off["hidden"] and off["txt"]=="",
              "　展開＝給示範句：", (not on["hidden"]) and on["txt"]==first)
        await c.shot("F-空隙暫停調樣式")
        await c.ev("(()=>{document.getElementById('vt-adv').open=false})()"); await asyncio.sleep(0.3)
        back=await c.ev("document.getElementById('vt-sub-preview').hidden")
        print("  收回去又留白：", back)

        print("【F3】暫停在句子裡：照樣顯示那一句（挑一句沒升格成卡的）")
        await c.ev("__vtSeek(16.0)"); await asyncio.sleep(0.4)
        r3=await c.ev("""(()=>{const t=16.0;const s=__vt.subs.find(x=>t>=x.start&&t<x.end);
          const isCard=s&&__vtCards().some(c=>Math.abs(c.start-s.start)<1e-6&&Math.abs(c.end-s.end)<1e-6);
          return {want:(s&&!isCard)?s.text:'', card:!!isCard,
                  got:document.getElementById('vt-sub-preview-text').textContent,
                  hidden:document.getElementById('vt-sub-preview').hidden}})()""")
        print("  期待「%s」實得「%s」隱藏=%s"%(r3["want"][:14],r3["got"][:14],r3["hidden"]))
        print("  正確：", r3["want"]==r3["got"] and not r3["hidden"])

        print("【F4】標題卡區間內：卡顯示、字幕不重複燒一次")
        cards=await c.ev("__vtCards()")
        cs=cards[0]["start"]; ce=cards[0]["end"]
        await c.ev("__vtSeek(%f)"%((cs+ce)/2)); await asyncio.sleep(0.4)
        r4=await c.ev("""(()=>({cards:document.querySelectorAll('#vt-card-layer .vt-card-view').length,
          subHidden:document.getElementById('vt-sub-preview').hidden}))()""")
        print(f"  播放頭 {round((cs+ce)/2,2)}s（卡 {cs}–{ce}）→ 卡疊層 {r4['cards']} 張、字幕隱藏 {r4['subHidden']}")
        print("  正確：", r4["cards"]==1 and r4["subHidden"])
        await c.shot("F-標題卡區間")

        print("【F5】回歸不變式")
        await c.ev("__vtAddCut(3.0,5.0)"); await asyncio.sleep(0.3)
        st=await c.ev("__vtStats()")
        print(f"  {st['duration']}−{st['cutTotal']}={st['finalDuration']}　成立：",
              abs(st["duration"]-st["cutTotal"]-st["finalDuration"])<1e-6)
        print("  句數：", await c.ev("__vt.subs.length"), "　卡數：", len(await c.ev("__vtCards()")))
        print("例外：",len(c.exc))
        for e in c.exc: print("   ",e[:250])
asyncio.run(main())
