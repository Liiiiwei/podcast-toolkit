#!/usr/bin/env python3
# CDP 走查：驗「字幕塊可在時間軸上拖」（option A）。
# 拖整塊＝平移出現時間、拖左右把手＝改起訖；夾在鄰句之間永不重疊/換序。
# 紀律：每個 ✓ 綁布林斷言（實得==期待）；真滑鼠座標（Input.dispatchMouseEvent）驅動；
#       末段兩個「改碼→重載→斷言翻紅→還原」突變測試，自證斷言真的在測東西。
import asyncio, base64, json, sys, shutil, math
import websockets

CDP = "ws://127.0.0.1:9223/devtools/browser/1ef93a4d-92f2-4611-a4c8-f5df8eb1554d"
URL = "http://127.0.0.1:8791/video-edit-prototype.html?demo=1"
OUT = "/Users/Mac365/conductor/workspaces/podcast-toolkit/colombo/.context"
REPO_JS = "/Users/Mac365/conductor/workspaces/podcast-toolkit/colombo/podcast_toolkit/web/static/video-edit-prototype.js"
SANDBOX_JS = "/private/tmp/vt-proto/video-edit-prototype.js"

def fmt(sec):  # 對齊頁面內 fmt(sec)：mm:ss.s
    sec = max(0.0, sec)
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:04.1f}"

class S:
    def __init__(self, ws, sid=None):
        self.ws=ws; self.sid=sid; self._id=0; self._p={}; self.events=[]
    async def send(self, method, params=None):
        self._id+=1; mid=self._id
        msg={"id":mid,"method":method,"params":params or {}}
        if self.sid: msg["sessionId"]=self.sid
        fut=asyncio.get_event_loop().create_future(); self._p[mid]=fut
        await self.ws.send(json.dumps(msg))
        return await asyncio.wait_for(fut, timeout=30)
    def _dispatch(self, d):
        if "id" in d and d["id"] in self._p: self._p.pop(d["id"]).set_result(d.get("result", d))
        elif "method" in d: self.events.append(d)

async def reader(ws, sessions):
    async for raw in ws:
        d=json.loads(raw)
        for s in sessions.values(): s._dispatch(d)

async def main():
    results=[]
    def check(name, ok, got=None, want=None):
        results.append((name, bool(ok), got, want))
        print(f"{'✓' if ok else '✗'} {name}" + ("" if got is None else f"  got={got!r} want={want!r}"), flush=True)

    ws=await websockets.connect(CDP, max_size=None)
    browser=S(ws); sessions={"b":browser}
    asyncio.create_task(reader(ws, sessions))
    t=await browser.send("Target.createTarget", {"url":"about:blank"})
    a=await browser.send("Target.attachToTarget", {"targetId":t["targetId"],"flatten":True})
    page=S(ws, a["sessionId"]); sessions[a["sessionId"]]=page
    await page.send("Page.enable"); await page.send("Runtime.enable"); await page.send("Log.enable")
    await page.send("Network.setCacheDisabled", {"cacheDisabled":True})
    await page.send("Emulation.setDeviceMetricsOverride", {"width":1440,"height":900,"deviceScaleFactor":1,"mobile":False})

    async def js(expr):
        r=await page.send("Runtime.evaluate", {"expression":expr,"returnByValue":True,"awaitPromise":True})
        if "exceptionDetails" in r: return {"__err__": r["exceptionDetails"].get("text"), "d": r["exceptionDetails"]}
        return r.get("result", {}).get("value")

    async def nav():
        page.events.clear()
        await page.send("Page.navigate", {"url":URL})
        for _ in range(60):
            if any(e["method"]=="Page.loadEventFired" for e in page.events): break
            await asyncio.sleep(0.2)
        await asyncio.sleep(1.8)

    async def mouse(kind, x, y, buttons):
        await page.send("Input.dispatchMouseEvent",
            {"type":kind,"x":float(x),"y":float(y),"button":"left","buttons":buttons,"clickCount":1})

    async def drag(cx, cy, dx, steps=8):
        await mouse("mousePressed", cx, cy, 1)
        for s in range(1, steps+1):
            await mouse("mouseMoved", cx + dx*s/steps, cy, 1)
        await mouse("mouseReleased", cx + dx, cy, 0)
        await asyncio.sleep(0.15)

    async def tap(cx, cy):
        await mouse("mousePressed", cx, cy, 1)
        await mouse("mouseReleased", cx, cy, 0)
        await asyncio.sleep(0.12)

    # rect + handle 幾何（每次重繪後都要重問，DOM 換新節點）
    RECT_JS = """(()=>{const K=%d;const b=[...document.querySelectorAll('#vt-sub-track .vt-sub')][K];
      if(!b)return null;const r=b.getBoundingClientRect();
      const hl=b.querySelector('.vt-sub-h.is-l'),hr=b.querySelector('.vt-sub-h.is-r');
      const g=n=>n?n.getBoundingClientRect():null;const rl=g(hl),rr=g(hr);
      const midY=r.top+r.height/2, midX=r.left+r.width/2;
      const topMid=document.elementFromPoint(midX,midY);
      const topR=document.elementFromPoint(r.right-3,midY);
      const topL=document.elementFromPoint(r.left+3,midY);
      return {left:r.left,right:r.right,top:r.top,h:r.height,w:r.width,midX:midX,midY:midY,
        lLeft:rl?rl.left:null,lW:rl?rl.width:null,rRight:rr?rr.right:null,rLeft:rr?rr.left:null,rW:rr?rr.width:null,
        opL:hl?parseFloat(getComputedStyle(hl).opacity):null,opR:hr?parseFloat(getComputedStyle(hr).opacity):null,
        bodyIsBlock: !!(topMid && topMid.closest && topMid.closest('.vt-sub') && !(topMid.closest('.vt-sub-h'))),
        topRIsHandleR: !!(topR && topR.closest && topR.closest('.vt-sub-h.is-r')),
        topLIsHandleL: !!(topL && topL.closest && topL.closest('.vt-sub-h.is-l'))};})()"""
    async def rect(k): return await js(RECT_JS % k)
    async def sub(k): return await js(f"(()=>{{const s=window.__vt.subs[{k}];return s?{{start:s.start,end:s.end,text:s.text}}:null;}})()")
    async def linetime(k): return await js(f"(()=>{{const r=document.querySelector('.vt-line[data-i=\"{k}\"] .vt-line-time');return r?r.textContent:null;}})()")

    # ===================== Phase A：真碼 =====================
    await nav()

    ready = await js("(()=>{const v=document.getElementById('vt-video');return {rs:v?v.readyState:-1,seek:v?v.seekable.length:-1,dur:window.__vt?window.__vt.duration:null,n:window.__vt?window.__vt.subs.length:0};})()")
    check("媒體可 seek 且字幕已載入（readyState>=1 且 seekable>0）",
          isinstance(ready,dict) and ready.get("rs",0)>=1 and ready.get("seek",0)>0 and ready.get("n",0)>0,
          got=ready, want="rs>=1,seek>0,n>0")
    DUR = ready["dur"]; N = ready["n"]

    pick = await js("""(()=>{const blocks=[...document.querySelectorAll('#vt-sub-track .vt-sub')];
      let best=-1,bw=0;for(let k=1;k<window.__vt.subs.length-1;k++){const b=blocks[k];if(!b)continue;
      const w=b.getBoundingClientRect().width;if(w>=40&&w>bw){bw=w;best=k;}}
      const track=document.getElementById('vt-sub-track');return {k:best,trackW:track.getBoundingClientRect().width};})()""")
    K = pick["k"]; TRACKW = pick["trackW"]; PPS = TRACKW / DUR  # px per second
    check("找到夠寬又有雙鄰句的測試字幕塊 K", isinstance(K,int) and 1<=K<=N-2, got={"K":K,"trackW":round(TRACKW,1),"pps":round(PPS,2)}, want="1<=K<=N-2")

    # ---- A1 幾何：左右把手貼齊、寬≈10、預設可見、elementFromPoint 命中 ----
    r = await rect(K)
    check("K 有左右把手且預設可見（opacity>=0.2）",
          isinstance(r,dict) and r.get("lW") and r.get("rW") and (r.get("opL") or 0)>=0.2 and (r.get("opR") or 0)>=0.2,
          got={"lW":r.get("lW"),"rW":r.get("rW"),"opL":r.get("opL"),"opR":r.get("opR")} if isinstance(r,dict) else r, want="有把手,op>=0.2")
    if isinstance(r,dict) and r.get("lW"):
        check("左把手貼齊塊左緣（|diff|<=2）", abs(r["lLeft"]-r["left"])<=2.0, got=round(abs(r["lLeft"]-r["left"]),2), want="<=2")
        check("右把手貼齊塊右緣（|diff|<=2）", abs(r["rRight"]-r["right"])<=2.0, got=round(abs(r["rRight"]-r["right"]),2), want="<=2")
        check("把手寬≈10px", 8.5<=r["rW"]<=11.5, got=round(r["rW"],2), want="8.5–11.5")
        check("塊中央最上層是塊身非把手（拖中央=平移）", r["bodyIsBlock"] is True, got=r["bodyIsBlock"], want=True)
        check("右緣最上層是 is-r 把手（elementFromPoint）", r["topRIsHandleR"] is True, got=r["topRIsHandleR"], want=True)
        check("左緣最上層是 is-l 把手（elementFromPoint）", r["topLIsHandleL"] is True, got=r["topLIsHandleL"], want=True)

    # ---- A2 拖整塊平移：start/end 同步位移、時長不變、右欄時間標籤同步、未跨鄰句 ----
    b0 = await sub(K); prevEnd = (await sub(K-1))["end"]; nextStart = (await sub(K+1))["start"]
    lbl_before = await linetime(K)
    dx = 0.4 * PPS  # 平移 0.4s，鄰句有 0.35–0.7s 空隙，不會撞牆
    r = await rect(K)
    await drag(r["midX"], r["midY"], dx)
    b1 = await sub(K); lbl_after = await linetime(K)
    moved = b1["start"] - b0["start"]
    check("拖整塊：start 右移了（≈0.4s）", 0.25 <= moved <= 0.55, got=round(moved,3), want="0.25–0.55s")
    check("拖整塊：時長不變（平移非縮放）", abs((b1["end"]-b1["start"])-(b0["end"]-b0["start"]))<=0.02, got=round((b1["end"]-b1["start"])-(b0["end"]-b0["start"]),3), want="~0")
    check("拖整塊後未跨過鄰句（start>=前句end 且 end<=後句start）",
          b1["start"]>=prevEnd-1e-6 and b1["end"]<=nextStart+1e-6, got={"start":round(b1["start"],3),"prevEnd":prevEnd,"end":round(b1["end"],3),"nextStart":nextStart}, want="夾在鄰句間")
    check("右欄時間標籤跟著變（單一資料源同步）", lbl_before is not None and lbl_after is not None and lbl_before!=lbl_after, got={"before":lbl_before,"after":lbl_after}, want="改變")
    check("右欄標籤數值＝新 start 的 fmt", lbl_after==fmt(b1["start"]), got=lbl_after, want=fmt(b1["start"]))

    # ---- A3 復原：一次拖只堆一個復原點，⌘Z 還原起訖 ----
    depth0 = await js("window.__vt.undoStack.length")
    base = await sub(K)
    r = await rect(K)
    await drag(r["midX"], r["midY"], 0.3*PPS)
    depth1 = await js("window.__vt.undoStack.length")
    check("一次拖只新增 1 個復原點（同 tag 去重）", depth1==depth0+1, got={"before":depth0,"after":depth1}, want="+1")
    await js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'z',metaKey:true,bubbles:true}))")
    await asyncio.sleep(0.2)
    aft = await sub(K)
    check("⌘Z 還原這句起訖回拖曳前", abs(aft["start"]-base["start"])<=0.01 and abs(aft["end"]-base["end"])<=0.01,
          got={"start":round(aft["start"],3),"end":round(aft["end"],3)}, want={"start":round(base["start"],3),"end":round(base["end"],3)})

    # ---- A4 左把手：只改 start、end 不動 ----
    b0 = await sub(K); r = await rect(K)
    lcx = r["lLeft"] + r["lW"]/2
    await drag(lcx, r["midY"], 0.3*PPS)
    b1 = await sub(K)
    check("拖左把手：start 增加", b1["start"]-b0["start"]>=0.15, got=round(b1["start"]-b0["start"],3), want=">=0.15")
    check("拖左把手：end 不動", abs(b1["end"]-b0["end"])<=0.02, got=round(b1["end"]-b0["end"],3), want="~0")

    # ---- A5 右把手：只改 end、start 不動 ----
    b0 = await sub(K); r = await rect(K)
    rcx = r["rRight"] - r["rW"]/2
    await drag(rcx, r["midY"], 0.3*PPS)
    b1 = await sub(K)
    check("拖右把手：end 增加", b1["end"]-b0["end"]>=0.15, got=round(b1["end"]-b0["end"],3), want=">=0.15")
    check("拖右把手：start 不動", abs(b1["start"]-b0["start"])<=0.02, got=round(b1["start"]-b0["start"],3), want="~0")

    # ---- A6 點一下（不拖）仍會跳到那句：currentTime→sub.start ----
    await js("document.getElementById('vt-video').currentTime=0"); await asyncio.sleep(0.15)
    b0 = await sub(K); r = await rect(K)
    await tap(r["midX"], r["midY"])
    ct = await js("document.getElementById('vt-video').currentTime")
    check("點一下（未拖）仍跳到該句 start", abs(ct-b0["start"])<=0.15, got=round(ct,3), want=round(b0["start"],3))

    # ---- A7 有拖曳（>4px）就不搶跳轉：currentTime 停在原處 ----
    await js("document.getElementById('vt-video').currentTime=0"); await asyncio.sleep(0.15)
    r = await rect(K)
    await drag(r["midX"], r["midY"], 0.3*PPS)
    ct = await js("document.getElementById('vt-video').currentTime")
    check("拖過就不觸發跳轉（__vtMoved 擋掉 click）", abs(ct-0)<=0.12, got=round(ct,3), want="~0")

    # ---- A8 MIN_SUB_DUR：左把手拉爆右邊，時長不小於 0.3s ----
    r = await rect(K)
    lcx = r["lLeft"] + r["lW"]/2
    await drag(lcx, r["midY"], 60*PPS)  # 用力往右拉
    b1 = await sub(K)
    check("左把手拉到底：時長夾在 MIN_SUB_DUR(0.3s)", abs((b1["end"]-b1["start"])-0.3)<=0.03, got=round(b1["end"]-b1["start"],3), want="~0.3")

    # ---- A9 鄰句夾制：整塊往右拉爆，end 停在後句 start（不重疊不換序）----
    nextStart = (await sub(K+1))["start"]
    r = await rect(K)
    await drag(r["midX"], r["midY"], 60*PPS)
    b1 = await sub(K)
    check("整塊拉爆：end 停在後句 start（不重疊）", b1["end"]<=nextStart+1e-3 and abs(b1["end"]-nextStart)<=0.05,
          got={"end":round(b1["end"],3),"nextStart":nextStart}, want="end≈nextStart")
    check("整塊拉爆：仍未換序（start<後句 start）", b1["start"]<nextStart, got=round(b1["start"],3), want=f"<{nextStart}")

    # console 錯誤
    errs=[]
    for e in page.events:
        if e["method"]=="Runtime.exceptionThrown":
            d=e["params"]["exceptionDetails"]; errs.append(d.get("exception",{}).get("description") or d.get("text"))
        if e["method"]=="Runtime.consoleAPICalled" and e["params"]["type"]=="error":
            errs.append(" ".join(str(x.get("value",x.get("description",""))) for x in e["params"]["args"]))
    check("Phase A 無 console 錯誤/例外", len(errs)==0, got=errs[:3], want=[])

    # 截圖
    shot=await page.send("Page.captureScreenshot", {"format":"png"})
    with open(f"{OUT}/09-subtitle-drag.png","wb") as f: f.write(base64.b64decode(shot["data"]))
    print("saved 09-subtitle-drag.png", flush=True)

    # ===================== Phase B：突變①拿掉鄰句夾制 → A9 該翻紅 =====================
    src = open(SANDBOX_JS, encoding="utf-8").read()
    OLD_CLAMP = '          const hi = Math.max(loBound, hiBound - dur);\n          sub.start = clamp(s0 + dt, loBound, hi);'
    assert OLD_CLAMP in src, "找不到夾制原碼，突變①無法進行"
    open(SANDBOX_JS,"w",encoding="utf-8").write(src.replace(OLD_CLAMP,
        '          const hi = Math.max(loBound, hiBound - dur);\n          sub.start = s0 + dt; // MUTATED: 拿掉夾制'))
    await nav()
    K2 = (await js("""(()=>{const blocks=[...document.querySelectorAll('#vt-sub-track .vt-sub')];
      let best=-1,bw=0;for(let k=1;k<window.__vt.subs.length-1;k++){const b=blocks[k];if(!b)continue;
      const w=b.getBoundingClientRect().width;if(w>=40&&w>bw){bw=w;best=k;}}return best;})()"""))
    nextStart2 = (await sub(K2+1))["start"]
    r = await rect(K2)
    await drag(r["midX"], r["midY"], 60*PPS)
    b1 = await sub(K2)
    overlaps = b1["end"] > nextStart2 + 0.05
    check("突變①（無夾制）→ 拉爆後 end 越過後句 start（A9 斷言確實在測夾制）",
          overlaps, got={"end":round(b1["end"],3),"nextStart":nextStart2}, want="end>nextStart")
    open(SANDBOX_JS,"w",encoding="utf-8").write(open(REPO_JS,encoding="utf-8").read())  # 還原

    # ===================== Phase C：突變②拿掉 __vtMoved 擋跳 → A7 該翻紅 =====================
    src = open(SANDBOX_JS, encoding="utf-8").read()
    OLD_GUARD = '        if (el.__vtMoved) {\n          el.__vtMoved = false;\n          return;\n        }'
    assert OLD_GUARD in src, "找不到 __vtMoved 擋跳原碼，突變②無法進行"
    open(SANDBOX_JS,"w",encoding="utf-8").write(src.replace(OLD_GUARD,
        '        el.__vtMoved = false; // MUTATED: 拿掉擋跳，拖完照樣 seek\n        // return 已移除'))
    await nav()
    K3 = (await js("""(()=>{const blocks=[...document.querySelectorAll('#vt-sub-track .vt-sub')];
      let best=-1,bw=0;for(let k=1;k<window.__vt.subs.length-1;k++){const b=blocks[k];if(!b)continue;
      const w=b.getBoundingClientRect().width;if(w>=40&&w>bw){bw=w;best=k;}}return best;})()"""))
    await js("document.getElementById('vt-video').currentTime=0"); await asyncio.sleep(0.15)
    b0 = await sub(K3); r = await rect(K3)
    await drag(r["midX"], r["midY"], 0.3*PPS)
    ct = await js("document.getElementById('vt-video').currentTime")
    jumped = abs(ct - b0["start"]) <= 0.2 and ct > 0.2
    check("突變②（無擋跳）→ 拖完仍跳到 start（A7 斷言確實在測 __vtMoved 擋跳）",
          jumped, got={"ct":round(ct,3),"newStart":round(b0["start"],3)}, want="ct≈start(非0)")
    open(SANDBOX_JS,"w",encoding="utf-8").write(open(REPO_JS,encoding="utf-8").read())  # 還原

    # 驗證還原成功（sandbox == repo）
    same = open(SANDBOX_JS,encoding="utf-8").read()==open(REPO_JS,encoding="utf-8").read()
    check("突變後 sandbox 已還原為 repo 版", same, got=same, want=True)

    passed=sum(1 for _,ok,_,_ in results if ok); total=len(results)
    print(f"\n=== {passed}/{total} 斷言通過 ===", flush=True)
    for name,ok,got,want in results:
        if not ok: print(f"  FAIL: {name}  got={got!r} want={want!r}", flush=True)
    await ws.close()
    sys.exit(0 if passed==total else 1)

asyncio.run(main())
