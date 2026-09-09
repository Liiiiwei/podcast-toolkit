import asyncio
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
URL="http://127.0.0.1:8877/static/video-edit-prototype.html?v=probe"

IDS=["vt-render","vt-render-cancel","vt-render-reveal","vt-render-close",
     "vt-toast","vt-card-inspector","vt-empty-note","vt-plan","vt-sub-preview"]

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))})
        for _ in range(60):
            if await c.ev("typeof window.__vtRender")=="function": break
            await asyncio.sleep(0.5)
        else: raise SystemExit("hook 沒出現")

        # 每個都強制設 hidden=true，量實際可見性（display 與盒子尺寸）
        js="""(()=>{const out={};
          for(const id of %s){const e=document.getElementById(id);
            if(!e){out[id]='(找不到)';continue}
            const was=e.hidden; e.hidden=true;
            const d=getComputedStyle(e).display; const r=e.getBoundingClientRect();
            out[id]={display:d, 寬高:[Math.round(r.width),Math.round(r.height)],
                     真的看不見:(d==='none'||r.width===0&&r.height===0)};
            e.hidden=was}
          return out})()""" % (str(IDS).replace("'",'"'))
        res=await c.ev(js)
        bad=[]
        for k,v in res.items():
            mark = "OK " if (isinstance(v,dict) and v["真的看不見"]) else "壞 "
            if isinstance(v,dict) and not v["真的看不見"]: bad.append(k)
            print(f"  {mark}{k:22s} {v}")
        print("\n設了 hidden 卻仍然看得見的元素：", bad if bad else "（無）")
        print("例外：",len(c.exc))
asyncio.run(main())
