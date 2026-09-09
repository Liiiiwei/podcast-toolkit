import asyncio, json, urllib.request, base64
import websockets
URL="http://127.0.0.1:8791/video-edit-prototype.html?demo=1&nocache=15"; PORT=9333
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
    pages=[t for t in hj("/json/list") if t["type"]=="page"]
    page=next((t for t in pages if "video-edit-prototype" in t["url"]), pages[-1])
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws); await c.send("Page.enable"); await c.send("Runtime.enable")
        await c.send("Network.enable"); await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Page.navigate",{"url":URL}); await asyncio.sleep(3.5)
        for _ in range(30):
            await asyncio.sleep(0.4)
            if await c.ev("(typeof __vtLines==='function')?__vtLines().length:0"): break
        async def mark(step):
            if c.exc:
                print(f">>> exception 在【{step}】："); [print(e[:900]) for e in c.exc]; c.exc.clear()
            else: print(f"    ✓ {step}")

        print("== A 右欄清單存在且直式 ==")
        lines=await c.ev("__vtLines()")
        print("  句數：",len(lines)," 前3列：",[(l["time"],l["text"]) for l in lines[:3]])
        print("  計數標籤：", await c.ev("document.getElementById('vt-line-count').textContent"))
        geo=await c.ev("""(()=>{const rows=[...document.querySelectorAll('#vt-line-list .vt-line')].slice(0,3).map(n=>{const r=n.getBoundingClientRect();return{t:Math.round(r.top),l:Math.round(r.left),h:Math.round(r.height)}});const side=document.getElementById('vt-style-panel').getBoundingClientRect();const stage=document.querySelector('.vt-stage').getBoundingClientRect();return{rows,sideL:Math.round(side.left),sideW:Math.round(side.width),sideH:Math.round(side.height),stageR:Math.round(stage.right),stageH:Math.round(stage.height)}})()""")
        print("  前3列 top：",[r["t"] for r in geo["rows"]],"（遞增＝直式）")
        print(f"  右欄 left={geo['sideL']} 寬={geo['sideW']} 高={geo['sideH']}；舞台右緣={geo['stageR']} 高={geo['stageH']}")
        print("  右欄在影片右半邊：", geo["sideL"]>=geo["stageR"])
        print("  清單可捲：", await c.ev("(()=>{const l=document.getElementById('vt-line-list');return{scrollH:l.scrollHeight,clientH:l.clientHeight,scrollable:l.scrollHeight>l.clientHeight}})()"))
        await mark("A 右欄結構")
        await c.shot("G1-右欄清單")

        print("== B 時間軸已無就地編輯／升格鈕 ==")
        print("  .vt-sub-edit：", await c.ev("document.querySelectorAll('.vt-sub-edit').length"),
              " .vt-sub-promote：", await c.ev("document.querySelectorAll('.vt-sub-promote').length"))
        print("  軌名：", await c.ev("Array.from(document.querySelectorAll('.vt-gutter-cell')).map(n=>n.textContent.trim())"))
        await mark("B 時間軸簡化")

        print("== C 點時間軸字幕塊 → 右欄對應列取得焦點 ==")
        r=await c.ev("(()=>{const s=document.querySelectorAll('#vt-sub-track .vt-sub')[2];const b=s.getBoundingClientRect();return{x:b.x+b.width/2,y:b.y+b.height/2,txt:s.querySelector('.vt-sub-txt').textContent}})()")
        print("  點第3塊：",r["txt"])
        await c.click(r["x"],r["y"]); await asyncio.sleep(0.4)
        print("  焦點列：", await c.ev("__vtEditBox()"))
        print("  時間軸該塊高亮：", await c.ev("document.querySelectorAll('#vt-sub-track .vt-sub.is-active').length"))
        await mark("C 時間軸→右欄聯動")

        print("== D 右欄真鍵盤斷句（Home + 8×→ + Enter）==")
        before=await c.ev("__vtSubs().length")
        await c.key("Home",36)
        for _ in range(8): await c.key("ArrowRight",39)
        await asyncio.sleep(0.2)
        await c.key("Enter",13); await asyncio.sleep(0.5)
        subs=await c.ev("__vtSubs()")
        print(f"  句數 {before} → {len(subs)}")
        for x in subs[2:4]: print(f"   {x['text']}　{round(x['start'],2)}–{round(x['end'],2)}")
        print("  斷句後焦點：", await c.ev("__vtEditBox()"))
        print("  清單第3/4列：", [ (l["time"],l["text"]) for l in (await c.ev("__vtLines()"))[2:4] ])
        await mark("D 斷句")
        await c.shot("G2-斷句後")

        print("== E 句首 Backspace 合併 ==")
        await c.key("Backspace",8); await asyncio.sleep(0.5)
        subs=await c.ev("__vtSubs()")
        print(f"  句數 → {len(subs)}　第3句：{subs[2]['text']} {round(subs[2]['start'],2)}–{round(subs[2]['end'],2)}")
        e=await c.ev("__vtEditBox()")
        print("  游標停在接縫：", e["caret"] if e else None)
        await mark("E 合併")

        print("== F 打字：游標不跳、時間軸同步 ==")
        await c.type("啊"); await asyncio.sleep(0.4)
        e=await c.ev("__vtEditBox()")
        print("  輸入後 caret：",e["caret"]," 值：",e["value"][:14])
        print("  時間軸第3塊文字：", await c.ev("document.querySelectorAll('#vt-sub-track .vt-sub-txt')[2].textContent")[:20] if False else await c.ev("document.querySelectorAll('#vt-sub-track .vt-sub-txt')[2].textContent"))
        # 還原
        await c.key("Backspace",8); await asyncio.sleep(0.3)
        await mark("F 打字同步")

        print("== G 「卡」鈕升格 + 剪除淡化 ==")
        r=await c.ev("(()=>{const b=[...document.querySelectorAll('#vt-line-list .vt-line')][2].querySelectorAll('.vt-line-tool');const g=b[2].getBoundingClientRect();return{x:g.x+g.width/2,y:g.y+g.height/2,labels:[...b].map(n=>n.getAttribute('aria-label')),dis:[...b].map(n=>n.disabled)}})()")
        print("  第3列工具：",r["labels"]," disabled：",r["dis"])
        await c.click(r["x"],r["y"]); await asyncio.sleep(0.5)
        cards=await c.ev("__vtCards()")
        print("  標題卡數：",len(cards)," 最後一張：",[(x["tpl"],x["text"],round(x["start"],2),round(x["end"],2)) for x in cards][-1:])
        print("  第1列工具 disabled：", await c.ev("[...document.querySelectorAll('#vt-line-list .vt-line')][0].querySelectorAll('.vt-line-tool')[1].disabled"))
        await c.ev("__vtAddCut(1.0, 3.0)"); await asyncio.sleep(0.4)
        ls=await c.ev("__vtLines()")
        print("  剪除 1–3s 後，右欄被淡化的列：",[l["text"][:8] for l in ls if l["cut"]]," 部分剪除：",[l["text"][:8] for l in ls if l["partial"]])
        print("  不變式：", await c.ev("(()=>{const s=__vtStats();return {dur:s.duration, cut:s.cutTotal, fin:s.finalDuration, ok: Math.abs(s.duration-s.cutTotal-s.finalDuration)<0.01}})()"))
        await mark("G 升格與剪除聯動")
        await c.shot("G3-總覽")
        print("== 最終 console exception：", len(c.exc))
asyncio.run(main())
