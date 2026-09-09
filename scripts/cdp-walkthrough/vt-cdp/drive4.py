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
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL}); await asyncio.sleep(4)
        o={}
        o["雙擊前文字"]=await c.ev("__vtSubs()[1].text")
        o["雙擊開編輯"]=await c.ev("""(()=>{const card=document.querySelectorAll('#vt-sub-track .vt-sub')[1];
          card.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
          const inp=document.querySelector('#vt-sub-track input, #vt-sub-track textarea, #vt-sub-track [contenteditable]');
          return {出現輸入框:!!inp, tag:inp&&inp.tagName, 值:inp&&(inp.value??inp.textContent)}})()""")
        o["改字並送出"]=await c.ev("""(()=>{const inp=document.querySelector('#vt-sub-track input, #vt-sub-track textarea, #vt-sub-track [contenteditable]');
          if(!inp) return '無輸入框';
          if('value' in inp) inp.value='【改過的字卡文字】'; else inp.textContent='【改過的字卡文字】';
          inp.dispatchEvent(new Event('input',{bubbles:true}));
          inp.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
          inp.dispatchEvent(new Event('blur',{bubbles:true}));
          return true})()""")
        await asyncio.sleep(0.5)
        o["雙擊後文字"]=await c.ev("__vtSubs()[1].text")
        o["軌上顯示"]=await c.ev("document.querySelectorAll('#vt-sub-track .vt-sub')[1].textContent.trim()")
        o["剪後軌同步"]=await c.ev("[...document.querySelectorAll('#vt-ripple-track .vt-sub')].map(e=>e.textContent.trim())[1]")
        o["exceptions"]=c.logs[-5:]
        r=await c.send("Page.captureScreenshot",{"format":"png","captureBeyondViewport":True,"clip":{"x":0,"y":1020,"width":1440,"height":300,"scale":2}})
        open("/private/tmp/vt-cdp/shots/E-字卡改字.png","wb").write(base64.b64decode(r["data"]))
        print(json.dumps(o,ensure_ascii=False,indent=1))
asyncio.run(main())
