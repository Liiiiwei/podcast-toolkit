import asyncio, json, urllib.request
import websockets
URL="http://127.0.0.1:8791/video-edit-prototype.html?demo=1"; PORT=9333
def hj(p):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{p}",timeout=10) as r: return json.loads(r.read())
class CDP:
    def __init__(s,ws): s.ws=ws; s.i=0; s.exc=[]
    async def send(s,m,p=None):
        s.i+=1; mid=s.i
        await s.ws.send(json.dumps({"id":mid,"method":m,"params":p or {}}))
        while True:
            msg=json.loads(await s.ws.recv())
            if msg.get("method")=="Runtime.exceptionThrown":
                d=msg["params"]["exceptionDetails"]
                s.exc.append({"text":d.get("text"),
                    "desc":(d.get("exception") or {}).get("description","")[:600],
                    "line":d.get("lineNumber"),"url":d.get("url")})
            elif msg.get("id")==mid:
                if "error" in msg: raise RuntimeError(msg["error"])
                return msg.get("result",{})
    async def ev(s,e):
        r=await s.send("Runtime.evaluate",{"expression":e,"returnByValue":True,"awaitPromise":True})
        if r.get("exceptionDetails"): return {"__error":r["exceptionDetails"].get("text")}
        return r.get("result",{}).get("value")
    async def click(s,x,y):
        for t in ("mousePressed","mouseReleased"):
            await s.send("Input.dispatchMouseEvent",{"type":t,"x":x,"y":y,"button":"left","clickCount":1})
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL}); await asyncio.sleep(4)
        o={}
        box=await c.ev("(()=>{const r=document.querySelectorAll('#vt-sub-track .vt-sub')[1].getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(box["x"],box["y"]); await asyncio.sleep(0.4)
        o["A_開編輯"]=await c.ev("!!document.querySelector('#vt-sub-track .vt-sub-input')")
        o["A_exc"]=list(c.exc); c.exc.clear()
        # 逐字打（模擬真打字）
        await c.send("Input.dispatchKeyEvent",{"type":"keyDown","key":"X","code":"KeyX","text":"X","windowsVirtualKeyCode":88})
        await c.send("Input.dispatchKeyEvent",{"type":"keyUp","key":"X","code":"KeyX","windowsVirtualKeyCode":88})
        await asyncio.sleep(0.3)
        o["B_打一鍵後"]=await c.ev("""(()=>{const inp=document.querySelector('#vt-sub-track .vt-sub-input');
           return {輸入框在:!!inp, 值:inp&&inp.value, state文字:__vtSubs()[1].text, editing:(window.__vt&&__vt.editingSub)}})()""")
        o["B_exc"]=list(c.exc); c.exc.clear()
        print(json.dumps(o,ensure_ascii=False,indent=1))
asyncio.run(main())
