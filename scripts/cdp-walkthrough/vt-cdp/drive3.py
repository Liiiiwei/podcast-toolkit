import asyncio, json, base64, urllib.request, os
import websockets
URL="http://127.0.0.1:8791/video-edit-prototype.html?demo=1"; PORT=9333
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
        if r.get("exceptionDetails"): return {"__error":r["exceptionDetails"].get("text")}
        return r.get("result",{}).get("value")
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL}); await asyncio.sleep(4)
        o={}
        o["說明文字殘留"]=await c.ev("document.querySelectorAll('.vt-style-note').length")
        o["剪除"]=await c.ev("(()=>{__vtAddCut(8.4,10.3);return __vtStats().finalText})()")
        await asyncio.sleep(0.6)
        o["三軌可見度"]=await c.ev("""(()=>{const g=[...document.querySelectorAll('.vt-gutter-cell')].map(e=>e.textContent.trim());
          const r=document.getElementById('vt-ripple-track').getBoundingClientRect();
          return {軌道標籤:g, 剪後軌bottom:Math.round(r.bottom), 視窗高:innerHeight, 需捲動:r.bottom>innerHeight}})()""")
        # 整頁截圖（含視窗外）
        m=await c.send("Page.getLayoutMetrics")
        h=int(m["cssContentSize"]["height"]); w=int(m["cssContentSize"]["width"])
        r=await c.send("Page.captureScreenshot",{"format":"png","captureBeyondViewport":True,
            "clip":{"x":0,"y":0,"width":w,"height":h,"scale":1.5}})
        open("/private/tmp/vt-cdp/shots/D-整頁精簡後.png","wb").write(base64.b64decode(r["data"]))
        o["整頁尺寸"]={"w":w,"h":h}
        o["console"]=c.logs[-10:]
        print(json.dumps(o,ensure_ascii=False,indent=1))
asyncio.run(main())
