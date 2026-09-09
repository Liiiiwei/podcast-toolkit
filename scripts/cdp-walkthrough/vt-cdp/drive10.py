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
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL}); await asyncio.sleep(3.5)
        async def mark(step):
            if c.exc:
                print(f">>> exception 在【{step}】：")
                for e in c.exc: print(e[:1200])
                c.exc.clear()
            else: print(f"    {step}: 乾淨")
        b=await box(c,1); await c.click(b["x"],b["y"]); await asyncio.sleep(0.4); await mark("步驟1 點卡1開編輯")
        await c.type("Podcast 後製其實沒那麼難"); await asyncio.sleep(0.3); await mark("步驟2 打字")
        await c.key("Enter",13); await asyncio.sleep(0.5); await mark("步驟3 Enter")
        b=await box(c,3); await c.click(b["x"],b["y"]); await asyncio.sleep(0.4); await mark("步驟4 點卡3")
        await c.type("XXXX"); await c.key("Escape",27); await asyncio.sleep(0.4); await mark("步驟5 Escape")
        b=await box(c,5); await c.click(b["x"],b["y"]); await asyncio.sleep(0.4); await mark("步驟6 點卡5")
        await c.type("點別處也要存"); await asyncio.sleep(0.2); await mark("步驟7 打字")
        await c.click(700,300); await asyncio.sleep(0.6); await mark("步驟8 點預覽區 blur")
        await c.ev("__vtAddCut(3.0,5.0)"); await asyncio.sleep(0.5); await mark("步驟9 addCut")
asyncio.run(main())
