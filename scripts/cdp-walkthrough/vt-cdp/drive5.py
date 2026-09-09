import asyncio, json, base64, urllib.request
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
            if msg.get("method")=="Runtime.exceptionThrown": s.logs.append(msg["params"]["exceptionDetails"].get("text",""))
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
    async def key(s,k,code,vk):
        for t in ("keyDown","keyUp"):
            await s.send("Input.dispatchKeyEvent",{"type":t,"key":k,"code":code,"windowsVirtualKeyCode":vk,"nativeVirtualKeyCode":vk})
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL}); await asyncio.sleep(4)
        o={}
        o["1_原文字"]=await c.ev("__vtSubs()[1].text")
        box=await c.ev("(()=>{const r=document.querySelectorAll('#vt-sub-track .vt-sub')[1].getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(box["x"],box["y"]); await asyncio.sleep(0.4)
        o["2_點擊後出現輸入框"]=await c.ev("""(()=>{const inp=document.querySelector('#vt-sub-track .vt-sub-input');
          return inp?{有:true,值:inp.value,已聚焦:document.activeElement===inp,已全選:inp.selectionStart===0&&inp.selectionEnd===inp.value.length}:{有:false}})()""")
        r=await c.send("Page.captureScreenshot",{"format":"png","captureBeyondViewport":True,"clip":{"x":0,"y":830,"width":1440,"height":180,"scale":2}})
        open("/private/tmp/vt-cdp/shots/E1-編輯中.png","wb").write(base64.b64decode(r["data"]))
        # 真鍵盤輸入
        await c.send("Input.insertText",{"text":"改過的字卡文字ABC"}); await asyncio.sleep(0.2)
        o["3_輸入中值"]=await c.ev("document.querySelector('#vt-sub-track .vt-sub-input')?.value")
        await c.key("Enter","Enter",13); await asyncio.sleep(0.5)
        o["4_送出後state"]=await c.ev("__vtSubs()[1].text")
        o["5_輸入框已收回"]=await c.ev("!document.querySelector('#vt-sub-track .vt-sub-input')")
        o["6_字幕軌顯示"]=await c.ev("document.querySelectorAll('#vt-sub-track .vt-sub')[1].querySelector('.vt-sub-txt').textContent")
        o["7_剪後軌同步"]=await c.ev("[...document.querySelectorAll('#vt-ripple-track .vt-sub')].map(e=>e.textContent.trim())[1]")
        r=await c.send("Page.captureScreenshot",{"format":"png","captureBeyondViewport":True,"clip":{"x":0,"y":830,"width":1440,"height":180,"scale":2}})
        open("/private/tmp/vt-cdp/shots/E2-改字後.png","wb").write(base64.b64decode(r["data"]))
        # Escape 取消測試
        box2=await c.ev("(()=>{const r=document.querySelectorAll('#vt-sub-track .vt-sub')[3].getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
        o["8_第4張原文字"]=await c.ev("__vtSubs()[3].text")
        await c.click(box2["x"],box2["y"]); await asyncio.sleep(0.3)
        await c.send("Input.insertText",{"text":"這段應該被取消"}); await asyncio.sleep(0.2)
        await c.key("Escape","Escape",27); await asyncio.sleep(0.4)
        o["9_Escape後文字"]=await c.ev("__vtSubs()[3].text")
        o["exceptions"]=c.logs[-5:]
        print(json.dumps(o,ensure_ascii=False,indent=1))
asyncio.run(main())
