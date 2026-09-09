import asyncio, json, urllib.request, base64
import websockets
URL="http://127.0.0.1:8791/video-edit-prototype.html?demo=1"; PORT=9333
SHOTS="/private/tmp/vt-cdp/shots"
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
        await s.send("Input.dispatchMouseEvent",{"type":"mouseMoved","x":x,"y":y})
        for t in ("mousePressed","mouseReleased"):
            await s.send("Input.dispatchMouseEvent",{"type":t,"x":x,"y":y,"button":"left","buttons":1,"clickCount":1})
    async def drag(s,x1,y1,x2,y2,steps=8):
        await s.send("Input.dispatchMouseEvent",{"type":"mouseMoved","x":x1,"y":y1})
        await s.send("Input.dispatchMouseEvent",{"type":"mousePressed","x":x1,"y":y1,"button":"left","buttons":1,"clickCount":1})
        for k in range(1,steps+1):
            await s.send("Input.dispatchMouseEvent",{"type":"mouseMoved","x":x1+(x2-x1)*k/steps,"y":y1+(y2-y1)*k/steps,"button":"left","buttons":1})
            await asyncio.sleep(0.02)
        await s.send("Input.dispatchMouseEvent",{"type":"mouseReleased","x":x2,"y":y2,"button":"left","buttons":0,"clickCount":1})
    async def key(s,k,vk,code=None):
        for t in ("rawKeyDown","keyUp"):
            await s.send("Input.dispatchKeyEvent",{"type":t,"key":k,"code":code or k,"windowsVirtualKeyCode":vk,"nativeVirtualKeyCode":vk})
    async def type(s,txt):
        for ch in txt:
            await s.send("Input.dispatchKeyEvent",{"type":"keyDown","key":ch,"text":ch})
            await s.send("Input.dispatchKeyEvent",{"type":"keyUp","key":ch})
    async def shot(s,name):
        r=await s.send("Page.captureScreenshot",{"format":"png"})
        open(f"{SHOTS}/{name}.png","wb").write(base64.b64decode(r["data"]))
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL}); await asyncio.sleep(3.5)
        async def mark(step):
            if c.exc:
                print(f">>> exception 在【{step}】："); [print(e[:900]) for e in c.exc]; c.exc.clear()
            else: print(f"    ✓ {step}")

        print("== A 軌道結構 ==")
        print("  軌名：", await c.ev("Array.from(document.querySelectorAll('.vt-gutter-cell')).map(n=>n.textContent.trim())"))
        print("  剪後軌殘留：", await c.ev("document.querySelectorAll('.vt-ripple, #vt-ripple-track').length"))
        print("  標題卡軌存在：", await c.ev("!!document.getElementById('vt-card-track')"))
        await mark("A 結構讀取")

        print("== B 一鍵升格（真滑鼠點 ⤴）==")
        b=await c.ev("(()=>{const s=document.querySelectorAll('#vt-sub-track .vt-sub')[2];const r=s.getBoundingClientRect();const u=s.querySelector('.vt-sub-promote').getBoundingClientRect();return {sx:r.x+8,sy:r.y+r.height/2,ux:u.x+u.width/2,uy:u.y+u.height/2,txt:s.querySelector('.vt-sub-txt')?.textContent}})()")
        print("  第3句：", b["txt"])
        await c.send("Input.dispatchMouseEvent",{"type":"mouseMoved","x":b["sx"],"y":b["sy"]}); await asyncio.sleep(0.2)
        await c.click(b["ux"],b["uy"]); await asyncio.sleep(0.5)
        cards=await c.ev("__vtCards()")
        print("  標題卡數：",len(cards)," 內容：",[(x["tpl"],x["text"],round(x["start"],2),round(x["end"],2)) for x in cards])
        print("  軌道塊：", await c.ev("__vtCardTrack()"))
        print("  疊層：", await c.ev("__vtCardView()"))
        await mark("B 升格")
        await c.shot("C1-升格後三軌")

        print("== C 大小滑桿（鍵盤右鍵 4 次 = +20%）==")
        before=await c.ev("__vtCardView().fontSize")
        r=await c.ev("(()=>{const e=document.getElementById('ct-scale');const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(r["x"],r["y"]); await asyncio.sleep(0.2)
        for _ in range(4): await c.key("ArrowRight",39); await asyncio.sleep(0.08)
        await asyncio.sleep(0.3)
        after=await c.ev("__vtCardView().fontSize")
        lbl=await c.ev("document.getElementById('ct-scale-val').textContent")
        sc=(await c.ev("__vtCards()"))[0]["scale"]
        print(f"  字級 {before} → {after}；滑桿顯示 {lbl}；scale={sc}")
        await mark("C 大小")

        print("== D 九宮格（點左下角按鈕）==")
        r=await c.ev("(()=>{const b=document.querySelectorAll('#ct-pos button')[6];const r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,dx:b.dataset.x,dy:b.dataset.y}})()")
        print("  目標點：", r["dx"], r["dy"])
        await c.click(r["x"],r["y"]); await asyncio.sleep(0.4)
        v=await c.ev("__vtCardView()")
        print("  疊層 left/top：", v["left"], v["top"], " 高亮數：", await c.ev("document.querySelectorAll('#ct-pos button.is-on').length"))
        await mark("D 九宮格")
        await c.shot("C2-左下角")

        print("== E 影片上拖曳 + 九宮格吸附 ==")
        g=await c.ev("(()=>{const e=document.querySelector('#vt-card-layer .vt-card-view');const r=e.getBoundingClientRect();const l=document.getElementById('vt-card-layer').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,lx:l.x,ly:l.y,lw:l.width,lh:l.height}})()")
        # 拖到「接近正中偏上」但差一點點，看放開會不會吸到 0.5/0.16
        tx=g["lx"]+g["lw"]*0.52; ty=g["ly"]+g["lh"]*0.19
        await c.drag(g["x"],g["y"],tx,ty); await asyncio.sleep(0.5)
        cd=(await c.ev("__vtCards()"))[0]
        print(f"  放開後 x={cd['x']:.4f} y={cd['y']:.4f}（拖到 0.52/0.19，吸附目標 0.5/0.16）")
        await mark("E 拖曳吸附")
        await c.shot("C3-拖曳吸附後")

        print("== F 拖版型到軌道（Input.dispatchDragEvent）==")
        await c.send("Input.setInterceptDrags",{"enabled":True})
        src=await c.ev("(()=>{const t=document.querySelector('#vt-tpl-grid .vt-tpl[data-tpl=\"lower\"]');const r=t.getBoundingClientRect();const k=document.getElementById('vt-card-track').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,kx:k.x+k.width*0.6,ky:k.y+k.height/2}})()")
        dd={"type":"dragEnter","x":src["kx"],"y":src["ky"],"data":{"items":[{"mimeType":"text/vt-tpl","data":"lower"}],"dragOperationsMask":1}}
        try:
            await c.send("Input.dispatchDragEvent",dict(dd,type="dragEnter"))
            await c.send("Input.dispatchDragEvent",dict(dd,type="dragOver"))
            await c.send("Input.dispatchDragEvent",dict(dd,type="drop"))
            await asyncio.sleep(0.5)
            cards=await c.ev("__vtCards()")
            print("  拖放後標題卡數：",len(cards)," 最後一張：",cards[-1]["tpl"],round(cards[-1]["start"],2),"→",round(cards[-1]["end"],2))
        except Exception as e:
            print("  dispatchDragEvent 失敗：",str(e)[:200])
        await mark("F 拖版型")
        await asyncio.sleep(0.3)
        await c.shot("C4-兩張卡")
        print("== 最終 console exception：", len(c.exc))
asyncio.run(main())
