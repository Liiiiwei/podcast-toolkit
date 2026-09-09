import asyncio, json, base64, urllib.request, subprocess, os, sys, time
import websockets

URL = "http://127.0.0.1:8791/video-edit-prototype.html?demo=1"
SHOTS = "/private/tmp/vt-cdp/shots"
PORT = 9333

def http_json(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=10) as r:
        return json.loads(r.read())

class CDP:
    def __init__(self, ws): self.ws=ws; self.i=0; self.logs=[]
    async def send(self, method, params=None):
        self.i+=1; mid=self.i
        await self.ws.send(json.dumps({"id":mid,"method":method,"params":params or {}}))
        while True:
            msg=json.loads(await self.ws.recv())
            if msg.get("method")=="Runtime.consoleAPICalled":
                a=msg["params"]
                self.logs.append((a["type"], " ".join(str(x.get("value", x.get("description",""))) for x in a.get("args",[]))))
            elif msg.get("method")=="Runtime.exceptionThrown":
                d=msg["params"]["exceptionDetails"]
                self.logs.append(("exception", d.get("text","")+" "+str(d.get("exception",{}).get("description",""))[:200]))
            elif msg.get("id")==mid:
                if "error" in msg: raise RuntimeError(msg["error"])
                return msg.get("result",{})
    async def ev(self, expr, awaitp=True):
        r=await self.send("Runtime.evaluate", {"expression":expr,"returnByValue":True,"awaitPromise":awaitp})
        res=r.get("result",{})
        if r.get("exceptionDetails"):
            return {"__error": r["exceptionDetails"].get("text"), "desc": str(res.get("description"))[:300]}
        return res.get("value")
    async def shot(self, name):
        r=await self.send("Page.captureScreenshot", {"format":"png"})
        p=os.path.join(SHOTS, name+".png")
        open(p,"wb").write(base64.b64decode(r["data"]))
        return p

async def main():
    tabs=http_json("/json/list")
    page=[t for t in tabs if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=60_000_000) as ws:
        c=CDP(ws)
        await c.send("Page.enable"); await c.send("Runtime.enable")
        await c.send("Emulation.setDeviceMetricsOverride", {"width":1440,"height":900,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate", {"url":URL})
        await asyncio.sleep(4.0)
        out={}
        out["初始_stats"]=await c.ev("JSON.parse(JSON.stringify(window.__vtStats ? __vtStats() : {missing:true}))")
        out["初始_字卡"]=await c.ev("(()=>{const s=__vtSubs?__vtSubs():null; return s? {總數:s.length, 前3:s.slice(0,3)} : 'no __vtSubs'})()")
        out["初始_字幕軌DOM"]=await c.ev("(()=>{const t=document.getElementById('vt-sub-track'); const r=document.getElementById('vt-ripple-track'); return {字幕軌子元素:t?t.children.length:null, 剪後軌子元素:r?r.children.length:null}})()")
        await c.shot("A-初始")

        out["剪除後_stats"]=await c.ev("(()=>{__vtAddCut(3.0,6.6); return JSON.parse(JSON.stringify(__vtStats()))})()")
        await asyncio.sleep(0.6)
        out["剪除後_字卡"]=await c.ev("(()=>{const s=__vtSubs(); return {總數:s.length, 被剪:s.filter(x=>x.cut||x.removed||x.isCut).length, 樣本:s.slice(0,6)}})()")
        out["剪除後_字幕軌DOM"]=await c.ev("(()=>{const t=document.getElementById('vt-sub-track'); const r=document.getElementById('vt-ripple-track'); return {字幕軌子元素:t.children.length, 剪後軌子元素:r.children.length, 剪後軌首塊left:r.children[0]?getComputedStyle(r.children[0]).left:null}})()")
        await c.shot("B-剪除一段")

        out["樣式_改前"]=await c.ev("JSON.parse(JSON.stringify({style:__vtStyle(), preview:__vtPreview()}))")
        out["樣式_改後"]=await c.ev("(()=>{__vtSetStyle({fontSize:64, primaryColor:'#ffd400', outlineColor:'#000000', outline:4, bold:true, alignment:2}); return JSON.parse(JSON.stringify({style:__vtStyle(), preview:__vtPreview()}))})()")
        await asyncio.sleep(0.5)
        await c.shot("C-樣式套用")
        out["console"]=c.logs[-25:]
        print(json.dumps(out, ensure_ascii=False, indent=1))

asyncio.run(main())
