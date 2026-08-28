import asyncio, json, base64, urllib.request, os
import websockets
URL="http://127.0.0.1:8791/video-edit-prototype.html?demo=1"; SHOTS="/private/tmp/vt-cdp/shots"; PORT=9333
def hj(p):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{p}",timeout=10) as r: return json.loads(r.read())
class CDP:
    def __init__(s,ws): s.ws=ws; s.i=0; s.logs=[]
    async def send(s,m,p=None):
        s.i+=1; mid=s.i
        await s.ws.send(json.dumps({"id":mid,"method":m,"params":p or {}}))
        while True:
            msg=json.loads(await s.ws.recv())
            if msg.get("method")=="Runtime.consoleAPICalled":
                a=msg["params"]; s.logs.append((a["type"]," ".join(str(x.get("value",x.get("description",""))) for x in a.get("args",[]))))
            elif msg.get("method")=="Runtime.exceptionThrown":
                s.logs.append(("exception",msg["params"]["exceptionDetails"].get("text","")))
            elif msg.get("id")==mid:
                if "error" in msg: raise RuntimeError(msg["error"])
                return msg.get("result",{})
    async def ev(s,e):
        r=await s.send("Runtime.evaluate",{"expression":e,"returnByValue":True,"awaitPromise":True})
        if r.get("exceptionDetails"): return {"__error":r["exceptionDetails"].get("text"),"d":str(r.get("result",{}).get("description"))[:300]}
        return r.get("result",{}).get("value")
    async def shot(s,n):
        r=await s.send("Page.captureScreenshot",{"format":"png"})
        open(os.path.join(SHOTS,n+".png"),"wb").write(base64.b64decode(r["data"])); return n
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=60_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":900,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL}); await asyncio.sleep(4)
        o={}
        # 1) 完全落在剪除區間內的字卡（8.55–10.15）→ 應標記為 cut
        o["剪除8.4-10.3"]=await c.ev("""(()=>{__vtAddCut(8.4,10.3);const s=__vtSubs();
          return {stats:__vtStats(), 各狀態:s.reduce((a,x)=>(a[x.status]=(a[x.status]||0)+1,a),{}),
                  該卡:s.find(x=>Math.abs(x.start-8.55)<0.01)}})()""")
        await asyncio.sleep(0.5)
        o["軌道DOM"]=await c.ev("""(()=>{const t=document.getElementById('vt-sub-track'),r=document.getElementById('vt-ripple-track');
          const cls=n=>[...n.children].map(e=>e.className);
          return {字幕軌:cls(t), 剪後軌數:r.children.length, 剪後軌末塊:r.children[r.children.length-1]?.textContent?.slice(0,20)}})()""")
        await c.shot("B2-剪掉整張字卡")
        # 2) 用真面板控制項改樣式（不是直接改 state）
        o["面板控制項"]=await c.ev("""[...document.querySelectorAll('#vt-style-panel [id^=st-]')].map(e=>({id:e.id,tag:e.tagName,type:e.type,val:e.value}))""")
        o["面板改字級前"]=await c.ev("__vtPreview().fontSize")
        o["面板改字級後"]=await c.ev("""(()=>{const el=document.getElementById('st-size'); if(!el) return 'no #st-size';
          el.value=110; el.dispatchEvent(new Event('input',{bubbles:true}));
          return {state字級:__vtStyle().font_size, 預覽px:__vtPreview().fontSize}})()""")
        o["面板改顏色"]=await c.ev("""(()=>{const el=document.getElementById('st-color')||document.querySelector('#vt-style-panel input[type=color]');
          if(!el) return 'no color input'; el.value='#ffd400'; el.dispatchEvent(new Event('input',{bubbles:true}));
          return {id:el.id, state色:__vtStyle().primary_colour_hex, 預覽色:__vtPreview().color}})()""")
        o["切底色塊"]=await c.ev("""(()=>{const b=[...document.querySelectorAll('#vt-style-panel button[data-v="3"]')][0];
          if(!b) return 'no border-style button'; b.click();
          return {state邊框:__vtStyle().border_style, 預覽hasBox:__vtPreview().hasBox, 預覽背景:__vtPreview().background}})()""")
        await asyncio.sleep(0.4); await c.shot("C2-樣式面板實測")
        o["console"]=c.logs[-15:]
        print(json.dumps(o,ensure_ascii=False,indent=1))
asyncio.run(main())
