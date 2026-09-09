#!/usr/bin/env python3
# CDP 走查：驗「移除 × 鈕 / handle 回正確右緣 / Delete 鍵刪卡」三項。
# 紀律：每個 ✓ 綁布林斷言（實得==期待）；含 Delete 的突變測試 + 自證（沒選卡時 Delete 不刪卡）。
import asyncio, base64, json, sys
import websockets

CDP = "ws://127.0.0.1:9223/devtools/browser/1ef93a4d-92f2-4611-a4c8-f5df8eb1554d"
URL = "http://127.0.0.1:8791/video-edit-prototype.html?demo=1"
OUT = "/Users/Mac365/conductor/workspaces/podcast-toolkit/colombo/.context"

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
    await page.send("Page.navigate", {"url":URL})
    for _ in range(60):
        if any(e["method"]=="Page.loadEventFired" for e in page.events): break
        await asyncio.sleep(0.2)
    await asyncio.sleep(1.8)

    async def js(expr):
        r=await page.send("Runtime.evaluate", {"expression":expr,"returnByValue":True,"awaitPromise":True})
        if "exceptionDetails" in r: return {"__err__": r["exceptionDetails"].get("text"), "d": r["exceptionDetails"]}
        return r.get("result", {}).get("value")

    # 版本指紋：新碼才有的「把手回到真正的右緣」註解對應行為 —— 這裡直接驗 × 已不在 DOM
    del_ct = await js("document.querySelectorAll('.vt-card-del').length")
    check("× 刪除鈕已從 DOM 移除", del_ct==0, got=del_ct, want=0)

    # 造兩張夠寬的卡：A 在 t=5、B 在 t=12（各 3 秒，wpx 遠大於 24）。B 選取後 A 變未選取。
    idA = await js("(window.__vtDropTpl('big',5)||{}).id")
    idB = await js("(window.__vtDropTpl('lower',12)||{}).id")
    n0  = await js("window.__vtCards().length")
    check("兩張標題卡已建立", isinstance(idA,int) and isinstance(idB,int) and n0>=2, got={"idA":idA,"idB":idB,"n":n0}, want=">=2 張")

    # A 目前未選取（B 才是選取中）—— 用來驗把手「預設就可見」
    selA_now = await js("window.__vt.selectedCard")
    check("造 B 後 A 為未選取態（驗預設把手用）", selA_now==idB, got=selA_now, want=idB)

    # 量 A 的卡塊與左右把手幾何
    geo = await js(f"""(()=>{{
      const cards=[...document.querySelectorAll('#vt-card-track .vt-card')];
      const el=cards.find(c=>c.title.includes('重點大字')) || cards[0];
      if(!el) return {{__none__:true}};
      const cr=el.getBoundingClientRect();
      const hl=el.querySelector('.vt-card-h.is-l');
      const hr=el.querySelector('.vt-card-h.is-r');
      const g=n=>n?n.getBoundingClientRect():null;
      const rr=g(hr), rl=g(hl);
      const opR = hr?parseFloat(getComputedStyle(hr).opacity):null;
      // elementFromPoint：右緣中點最上層必須是 is-r 把手（不是卡身、不是別的）
      const midY=cr.top+cr.height/2;
      const topRight=document.elementFromPoint(cr.right-3, midY);
      const topRightIsHandle = !!(topRight && topRight.closest && topRight.closest('.vt-card-h.is-r'));
      const topLeft=document.elementFromPoint(cr.left+3, midY);
      const topLeftIsHandle = !!(topLeft && topLeft.closest && topLeft.closest('.vt-card-h.is-l'));
      return {{
        cardRight:cr.right, cardLeft:cr.left, cardW:cr.width,
        hasR:!!hr, hasL:!!hl,
        rRight: rr?rr.right:null, rWidth: rr?rr.width:null,
        lLeft: rl?rl.left:null, lWidth: rl?rl.width:null,
        opR, topRightIsHandle, topLeftIsHandle
      }};
    }})()""")
    check("A 有左右兩個把手", isinstance(geo,dict) and geo.get("hasL") and geo.get("hasR"), got=geo, want="hasL&hasR")
    if isinstance(geo,dict) and "cardRight" in geo:
        dR = abs(geo["rRight"]-geo["cardRight"])
        check("右把手貼齊卡右緣（不再被 × 頂成 right:15px）", dR<=2.0, got=round(dR,2), want="<=2px")
        dL = abs(geo["lLeft"]-geo["cardLeft"])
        check("左把手貼齊卡左緣", dL<=2.0, got=round(dL,2), want="<=2px")
        check("把手寬 ≈10px（比舊 7px 好抓）", 9.0<=geo["rWidth"]<=11.0, got=round(geo["rWidth"],2), want="9–11px")
        check("把手預設就可見（opacity≈0.45，非 0）", geo["opR"] is not None and geo["opR"]>=0.2, got=geo["opR"], want=">=0.2")
        check("右緣中點最上層是 is-r 把手（elementFromPoint）", geo["topRightIsHandle"] is True, got=geo["topRightIsHandle"], want=True)
        check("左緣中點最上層是 is-l 把手（elementFromPoint）", geo["topLeftIsHandle"] is True, got=geo["topLeftIsHandle"], want=True)

    # ── 突變測試：選取 A → 按 Delete → A 被刪、選取歸零、只少一張 ──
    await js(f"window.__vtSelectCard({idA})")
    sel_before = await js("window.__vt.selectedCard")
    n_before = await js("window.__vtCards().length")
    check("Delete 前：A 已選取", sel_before==idA, got=sel_before, want=idA)
    await js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Delete',bubbles:true}))")
    await asyncio.sleep(0.2)
    n_after = await js("window.__vtCards().length")
    a_gone = await js(f"window.__vtCards().every(c=>c.id!=={idA})")
    sel_after = await js("window.__vt.selectedCard")
    check("Delete 鍵刪掉選取中的標題卡（少一張）", n_after==n_before-1, got={"before":n_before,"after":n_after}, want="after=before-1")
    check("被刪的正是 A（A 已不在清單）", a_gone is True, got=a_gone, want=True)
    check("刪後選取歸零", sel_after in (None,0) and sel_after!=idA, got=sel_after, want="null")

    # ── 自證：此刻沒選卡，再按 Delete 不該再刪任何卡（證明是選取閘門，不是 Delete 恆刪卡）──
    n_mid = await js("window.__vtCards().length")
    await js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Delete',bubbles:true}))")
    await asyncio.sleep(0.2)
    n_end = await js("window.__vtCards().length")
    check("自證：沒選卡時 Delete 不刪卡（走剪除分支）", n_end==n_mid, got={"mid":n_mid,"end":n_end}, want="不變")

    # console 錯誤
    errs=[]
    for e in page.events:
        if e["method"]=="Runtime.exceptionThrown":
            d=e["params"]["exceptionDetails"]; errs.append(d.get("exception",{}).get("description") or d.get("text"))
        if e["method"]=="Runtime.consoleAPICalled" and e["params"]["type"]=="error":
            errs.append(" ".join(str(x.get("value",x.get("description",""))) for x in e["params"]["args"]))
    check("無 console 錯誤/例外", len(errs)==0, got=errs[:3], want=[])

    # 截圖：把 B 選取讓把手加深，截整頁看時間軸卡塊
    await js(f"window.__vtSelectCard({idB})"); await asyncio.sleep(0.3)
    shot=await page.send("Page.captureScreenshot", {"format":"png"})
    with open(f"{OUT}/08-card-handles-no-x.png","wb") as f: f.write(base64.b64decode(shot["data"]))
    print("saved 08-card-handles-no-x.png", flush=True)

    passed=sum(1 for _,ok,_,_ in results if ok); total=len(results)
    print(f"\n=== {passed}/{total} 斷言通過 ===", flush=True)
    for name,ok,got,want in results:
        if not ok: print(f"  FAIL: {name}  got={got!r} want={want!r}", flush=True)
    await ws.close()
    sys.exit(0 if passed==total else 1)

asyncio.run(main())
