import asyncio, json, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
URL="http://127.0.0.1:8877/static/video-edit-prototype.html?v=probe"
STUB=open("/private/tmp/vt-cdp/drive33.py").read().split('STUB = """')[1].split('"""')[0]
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&n="+str(id(c))})
        for _ in range(60):
            if await c.ev("typeof window.__vtRender")=="function": break
            await asyncio.sleep(0.5)
        await c.ev(STUB)
        await c.ev('window.__seq=[{"state":"done","percent":100.0,"eta_s":0,"output_files":["/tmp/ep/03_成品/端對端測試_YT完整版.mp4"]}]')
        b=await c.ev("(()=>{const r=document.getElementById('vt-export').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(b["x"],b["y"])
        for _ in range(40):
            s=await c.ev("__vtRender()")
            if s["polling"] is False and s["hidden"] is False: break
            await asyncio.sleep(0.2)
        print("done 態 __vtRender：", s)
        info=await c.ev("""(()=>{const out={};
          for(const id of ['vt-render-cancel','vt-render-reveal','vt-render-close']){
            const e=document.getElementById(id); const r=e.getBoundingClientRect();
            out[id]={hidden:e.hidden, 文字:e.textContent, 寬高:[Math.round(r.width),Math.round(r.height)],
                     計算display:getComputedStyle(e).display,
                     最上層是自己:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===e};
          }
          const box=document.getElementById('vt-render').getBoundingClientRect();
          out.進度區框=[Math.round(box.x),Math.round(box.y),Math.round(box.width),Math.round(box.height)];
          return out})()""")
        for k,v in info.items(): print("  ",k,"：",v)
        r=await c.send("Page.captureScreenshot",{"format":"png","clip":{
            "x":info["進度區框"][0]-6,"y":info["進度區框"][1]-10,
            "width":info["進度區框"][2]+330,"height":info["進度區框"][3]+20,"scale":3}})
        open("/private/tmp/vt-cdp/shots/P-完成態頂列.png","wb").write(base64.b64decode(r["data"]))
        print("  裁切圖已存 P-完成態頂列.png")
asyncio.run(main())
