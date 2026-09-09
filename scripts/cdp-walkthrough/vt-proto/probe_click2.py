import asyncio, json
import websockets
CDP="ws://127.0.0.1:9223/devtools/browser/1ef93a4d-92f2-4611-a4c8-f5df8eb1554d"
URL="http://127.0.0.1:8791/video-edit-prototype.html?demo=1"
class S:
    def __init__(s,ws,sid=None):s.ws=ws;s.sid=sid;s._id=0;s._p={};s.events=[]
    async def send(s,m,p=None):
        s._id+=1;i=s._id;msg={"id":i,"method":m,"params":p or {}}
        if s.sid:msg["sessionId"]=s.sid
        f=asyncio.get_event_loop().create_future();s._p[i]=f
        await s.ws.send(json.dumps(msg));return await asyncio.wait_for(f,timeout=30)
    def _d(s,d):
        if "id" in d and d["id"] in s._p:s._p.pop(d["id"]).set_result(d.get("result",d))
        elif "method" in d:s.events.append(d)
async def rd(ws,ss):
    async for raw in ws:
        d=json.loads(raw)
        for s in ss.values():s._d(d)
async def main():
    ws=await websockets.connect(CDP,max_size=None);br=S(ws);ss={"b":br}
    asyncio.create_task(rd(ws,ss))
    t=await br.send("Target.createTarget",{"url":"about:blank"})
    a=await br.send("Target.attachToTarget",{"targetId":t["targetId"],"flatten":True})
    pg=S(ws,a["sessionId"]);ss[a["sessionId"]]=pg
    await pg.send("Page.enable");await pg.send("Runtime.enable")
    await pg.send("Network.setCacheDisabled",{"cacheDisabled":True})
    await pg.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":900,"deviceScaleFactor":1,"mobile":False})
    await pg.send("Page.navigate",{"url":URL})
    for _ in range(60):
        if any(e["method"]=="Page.loadEventFired" for e in pg.events):break
        await asyncio.sleep(0.2)
    await asyncio.sleep(1.8)
    async def js(e):
        r=await pg.send("Runtime.evaluate",{"expression":e,"returnByValue":True,"awaitPromise":True})
        if "exceptionDetails" in r:return {"__err__":r["exceptionDetails"].get("text")}
        return r.get("result",{}).get("value")
    async def mouse(k,x,y,b):
        await pg.send("Input.dispatchMouseEvent",{"type":k,"x":float(x),"y":float(y),"button":"left","buttons":b,"clickCount":1})
    await js("""window.__log=[];
      // 掛一顆穩定的、pointerup 不重建 DOM 的探針按鈕，蓋在畫面左上
      const btn=document.createElement('button');btn.id='__probe';btn.textContent='p';
      btn.style.cssText='position:fixed;left:20px;top:20px;width:60px;height:60px;z-index:99999';
      btn.addEventListener('click',()=>window.__log.push('probe-click'));
      document.body.appendChild(btn);
      document.addEventListener('click',e=>window.__log.push(['doc-click',(e.target&&e.target.id)||(e.target&&e.target.className)||'?']),true);
    """)
    # tap 穩定探針按鈕（pointerup 不重建 DOM）
    await mouse("mousePressed",50,50,1)
    await mouse("mouseReleased",50,50,0)
    await asyncio.sleep(0.2)
    print("control (stable button) log:",json.dumps(await js("window.__log"),ensure_ascii=False))
    await ws.close()
asyncio.run(main())
