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
                s.exc.append((msg["params"]["exceptionDetails"].get("exception") or {}).get("description",""))
            elif msg.get("id")==mid:
                if "error" in msg: raise RuntimeError(msg["error"])
                return msg.get("result",{})
    async def ev(s,e):
        r=await s.send("Runtime.evaluate",{"expression":e,"returnByValue":True,"awaitPromise":True})
        return r.get("result",{}).get("value")
    async def click(s,x,y):
        for t in ("mousePressed","mouseReleased"):
            await s.send("Input.dispatchMouseEvent",{"type":t,"x":x,"y":y,"button":"left","clickCount":1})
    async def type(s,txt):
        for ch in txt:
            await s.send("Input.dispatchKeyEvent",{"type":"keyDown","key":ch,"text":ch})
            await s.send("Input.dispatchKeyEvent",{"type":"keyUp","key":ch})
    async def key(s,k,vk):
        for t in ("rawKeyDown","keyUp"):
            await s.send("Input.dispatchKeyEvent",{"type":t,"key":k,"code":k,"windowsVirtualKeyCode":vk,"nativeVirtualKeyCode":vk})
async def box(c,i): return await c.ev(f"(()=>{{const r=document.querySelectorAll('#vt-sub-track .vt-sub')[{i}].getBoundingClientRect();return {{x:r.x+r.width/2,y:r.y+r.height/2}}}})()")
async def run(name, fn):
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL}); await asyncio.sleep(3.5)
        c.exc.clear()
        await fn(c)
        await asyncio.sleep(0.5)
        print(f"### {name}: exception {len(c.exc)}")
        for e in c.exc: print(e[:900]);print("--")
async def s_enter(c):
    b=await box(c,1); await c.click(b["x"],b["y"]); await asyncio.sleep(0.4)
    await c.type("測試"); await c.key("Enter",13)
async def s_esc(c):
    b=await box(c,3); await c.click(b["x"],b["y"]); await asyncio.sleep(0.4)
    await c.type("XX"); await c.key("Escape",27)
async def s_switch(c):
    b=await box(c,1); await c.click(b["x"],b["y"]); await asyncio.sleep(0.4)
    b2=await box(c,4); await c.click(b2["x"],b2["y"])
async def s_addcut(c):
    await c.ev("__vtAddCut(3.0,5.0)")
async def main():
    for n,f in [("Enter送出",s_enter),("Escape取消",s_esc),("編輯中點另一張卡",s_switch),("addCut",s_addcut)]:
        await run(n,f)
asyncio.run(main())
