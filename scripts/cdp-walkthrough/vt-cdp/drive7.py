import asyncio, json, base64, urllib.request
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
                s.exc.append((d.get("exception") or {}).get("description","")[:300] or d.get("text"))
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
    async def type(s,txt):
        for ch in txt:
            await s.send("Input.dispatchKeyEvent",{"type":"keyDown","key":ch,"text":ch})
            await s.send("Input.dispatchKeyEvent",{"type":"keyUp","key":ch})
    async def key(s,k,vk):
        for t in ("rawKeyDown","keyUp"):
            await s.send("Input.dispatchKeyEvent",{"type":t,"key":k,"code":k,"windowsVirtualKeyCode":vk,"nativeVirtualKeyCode":vk})
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL}); await asyncio.sleep(4)
        o={}
        async def cardbox(i): return await c.ev(f"(()=>{{const r=document.querySelectorAll('#vt-sub-track .vt-sub')[{i}].getBoundingClientRect();return {{x:r.x+r.width/2,y:r.y+r.height/2}}}})()")
        # ── 情境 1：改字並 Enter 送出
        o["1_原文字"]=await c.ev("__vtSubs()[1].text")
        b=await cardbox(1); await c.click(b["x"],b["y"]); await asyncio.sleep(0.4)
        o["2_編輯態"]=await c.ev("(()=>{const i=document.querySelector('#vt-sub-track .vt-sub-input');return i?{值:i.value,聚焦:document.activeElement===i,全選:i.selectionEnd-i.selectionStart===i.value.length}:null})()")
        r=await c.send("Page.captureScreenshot",{"format":"png","clip":{"x":0,"y":840,"width":1440,"height":160,"scale":2}})
        open("/private/tmp/vt-cdp/shots/E1-編輯中.png","wb").write(base64.b64decode(r["data"]))
        await c.type("Podcast 後製其實沒那麼難"); await asyncio.sleep(0.3)
        o["3_輸入後值"]=await c.ev("document.querySelector('#vt-sub-track .vt-sub-input')?.value")
        await c.key("Enter",13); await asyncio.sleep(0.5)
        o["4_送出後_state"]=await c.ev("__vtSubs()[1].text")
        o["5_輸入框收回"]=await c.ev("!document.querySelector('#vt-sub-track .vt-sub-input')")
        o["6_字幕軌顯示"]=await c.ev("document.querySelectorAll('#vt-sub-track .vt-sub')[1].querySelector('.vt-sub-txt').textContent")
        o["7_剪後軌同步"]=await c.ev("[...document.querySelectorAll('#vt-ripple-track .vt-sub')][1].textContent.trim()")
        r=await c.send("Page.captureScreenshot",{"format":"png","clip":{"x":0,"y":840,"width":1440,"height":160,"scale":2}})
        open("/private/tmp/vt-cdp/shots/E2-改字後.png","wb").write(base64.b64decode(r["data"]))
        # ── 情境 2：Escape 取消
        o["8_第4張原文字"]=await c.ev("__vtSubs()[3].text")
        b=await cardbox(3); await c.click(b["x"],b["y"]); await asyncio.sleep(0.4)
        await c.type("XXXX"); await asyncio.sleep(0.2)
        o["9_取消前輸入框值"]=await c.ev("document.querySelector('#vt-sub-track .vt-sub-input')?.value")
        await c.key("Escape",27); await asyncio.sleep(0.4)
        o["10_Escape後_state"]=await c.ev("__vtSubs()[3].text")
        # ── 情境 3：blur 也送出
        o["11_第6張原文字"]=await c.ev("__vtSubs()[5].text")
        b=await cardbox(5); await c.click(b["x"],b["y"]); await asyncio.sleep(0.4)
        await c.type("點別處也要存"); await asyncio.sleep(0.2)
        await c.click(700,300); await asyncio.sleep(0.5)
        o["12_blur後_state"]=await c.ev("__vtSubs()[5].text")
        # ── 情境 4：改完字後剪除仍正常（不變式）
        o["13_剪除前總長"]=await c.ev("__vtStats().finalText")
        await c.ev("__vtAddCut(3.0,5.0)"); await asyncio.sleep(0.4)
        o["14_剪除後"]=await c.ev("(()=>{const s=__vtStats();return {原長:s.origText,剪後:s.finalText,段數:s.cutCount}})()")
        o["exceptions"]=c.exc
        print(json.dumps(o,ensure_ascii=False,indent=1))
asyncio.run(main())
