import asyncio, json
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
# 真後端（8877 直接讀 repo static），且不加 demo=1：source 要是 real 才不會跳 confirm
URL="http://127.0.0.1:8877/static/video-edit-prototype.html?v=32"

MSG="""(()=>{const e=document.getElementById('vt-export-msg'),p=document.getElementById('vt-plan-msg');
  const dis=id=>{const b=document.getElementById(id);return b?b.disabled:null};
  return {text:e.textContent, kind:e.className, planText:p.textContent, planKind:p.className,
          exportDisabled:dis('vt-export'), pushDisabled:dis('vt-plan-push'),
          renderDisabled:dis('vt-plan-render'), applyDisabled:dis('vt-plan-apply')}})()"""

async def click_export(c):
    b=await c.ev("(()=>{const r=document.getElementById('vt-export').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
    await c.click(b["x"],b["y"])

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))})
        for _ in range(80):
            if await c.ev("typeof window.__vtPlan==='function'"): break
            await asyncio.sleep(0.25)
        else: raise SystemExit("hook 沒出現 —— 來源未同步或頁面沒載起來")

        print("【K0】版本指紋（沒有＝來源沒同步，後面全部不算數）")
        src=await c.ev("__vtPlan().source")
        fp={"__vtPlanPush":await c.ev("typeof window.__vtPlanPush"),
            "輸出影片鈕":await c.ev("!!document.getElementById('vt-export')"),
            "輸出訊息位":await c.ev("!!document.getElementById('vt-export-msg')"),
            "source":src}
        print("  ",fp)
        if fp["輸出影片鈕"] is not True or src!="real":
            raise SystemExit(f"指紋不符（source={src}，real 才不會跳 confirm）")

        print("【K1】empty 態：還沒送出前訊息是空的、按鈕可按")
        m0=await c.ev(MSG)
        ok1 = m0["text"]=="" and m0["exportDisabled"] is False
        print("  ",m0); print("  K1 通過：",ok1)

        print("【K2】造資料：升格 2 句成卡 ＋ 一張空白卡（要驗「略過 N 張空白卡」）")
        await c.ev("__vtPromote(2)"); await asyncio.sleep(0.3)
        await c.ev("__vtPromote(5)"); await asyncio.sleep(0.3)
        await c.ev("__vtDropTpl('quote', 18.0)"); await asyncio.sleep(0.4)
        await c.ev("(()=>{const k=__vt.cards.find(c=>!c.src);if(k)k.text='';})()")
        await c.ev("__vtAddCut(4.0,6.0)"); await asyncio.sleep(0.3)
        p=await c.ev("__vtPlan()")
        n_card=len(p["cards"]); n_empty=sum(1 for x in p["cards"] if not (x.get("text") or "").strip())
        print(f"  卡 {n_card} 張（其中空白 {n_empty} 張）、剪除 {len(p['cuts'])} 段、字幕 {len(p['subtitles'])} 句")
        want_cards = n_card - n_empty

        print("【K3】loading 態：延遲後端 2.5 秒，看得到鎖鈕與「送出並合成中…」")
        await c.ev("""(()=>{window.__of=window.fetch;
          window.fetch=(...a)=>new Promise(r=>setTimeout(()=>r(window.__of(...a)),2500))})()""")
        await click_export(c); await asyncio.sleep(0.6)
        m1=await c.ev(MSG)
        ok3 = ("is-busy" in m1["kind"] and "合成中" in m1["text"]
               and m1["exportDisabled"] is True and m1["pushDisabled"] is True
               and m1["renderDisabled"] is True and m1["applyDisabled"] is True)
        print("  ",m1)
        print("  四顆鈕全鎖：", all(m1[k] is True for k in ("exportDisabled","pushDisabled","renderDisabled","applyDisabled")))
        print("  K3 通過：",ok3)
        await c.shot("K-送出中鎖鈕")

        print("【K4】success 態：訊息要講出標題卡張數")
        for _ in range(60):
            m2=await c.ev(MSG)
            if "is-busy" not in m2["kind"]: break
            await asyncio.sleep(0.5)
        print("  訊息：",m2["text"])
        print("  面板內同步：",m2["planText"])
        ok4 = ("is-ok" in m2["kind"] and f"標題卡 {want_cards} 張" in m2["text"]
               and m2["exportDisabled"] is False
               and (n_empty==0 or f"略過 {n_empty} 張空白卡" in m2["text"])
               and "已開始合成影片" in m2["text"])
        print(f"  含「標題卡 {want_cards} 張」：", f"標題卡 {want_cards} 張" in m2["text"],
              f"　含「略過 {n_empty} 張空白卡」：", f"略過 {n_empty} 張空白卡" in m2["text"],
              "　含「已開始合成影片」：", "已開始合成影片" in m2["text"],
              "　鈕已解鎖：", m2["exportDisabled"] is False)
        print("  兩處訊息一致：", m2["text"]==m2["planText"])
        print("  K4 通過：",ok4)
        await c.shot("K-輸出成功訊息")

        print("【K5】落地：後端真的把卡寫進 episode.yaml")
        import subprocess, re
        y=open("/private/tmp/pt-e2e/20260825 端對端測試/episode.yaml",encoding="utf-8").read()
        got=len(re.findall(r"^\s*-\s*\{?start:", y, re.M)) if "title_cards" in y else 0
        seg=y.split("title_cards:")[1] if "title_cards" in y else ""
        got=len([l for l in seg.splitlines() if l.strip().startswith("- ")])
        print(f"  episode.yaml 的 title_cards：{got} 張（期待 {want_cards}，空白卡不寫進去）")
        ok5 = got==want_cards
        print("  K5 通過：",ok5)

        print("【K6】error 態：後端回 500 → 看得見、不假裝成功、狀態零改動")
        before=await c.ev("(()=>{const p=__vtPlan();return{c:p.cuts.length,s:p.subtitles.length,k:p.cards.length}})()")
        await c.ev("""(()=>{window.fetch=()=>Promise.resolve(new Response(
          JSON.stringify({detail:"故意的後端錯誤"}),{status:500,headers:{"Content-Type":"application/json"}}))})()""")
        await click_export(c); await asyncio.sleep(1.2)
        m3=await c.ev(MSG)
        after=await c.ev("(()=>{const p=__vtPlan();return{c:p.cuts.length,s:p.subtitles.length,k:p.cards.length}})()")
        print("  ",m3)
        ok6 = ("is-ok" not in m3["kind"] and "is-busy" not in m3["kind"]
               and "故意的後端錯誤" in m3["text"] and m3["exportDisabled"] is False and before==after)
        print("  錯誤看得見：","故意的後端錯誤" in m3["text"],
              "　沒被誤標成功：","is-ok" not in m3["kind"],
              "　鈕已解鎖：",m3["exportDisabled"] is False,
              "　狀態零改動：",before==after)
        print("  K6 通過：",ok6)
        await c.shot("K-輸出失敗訊息")

        print("【K7】連不上後端要講人話（不是 HTTP 錯，是根本沒接上）")
        await c.ev("(()=>{window.fetch=()=>Promise.reject(new TypeError('Failed to fetch'))})()")
        await click_export(c); await asyncio.sleep(1.0)
        m4=await c.ev(MSG)
        ok7 = "連不上後端" in m4["text"] and m4["exportDisabled"] is False
        print("  ",m4["text"]); print("  K7 通過：",ok7)
        await c.ev("(()=>{if(window.__of)window.fetch=window.__of})()")

        print("【K8】回歸不變式")
        st=await c.ev("__vtStats()")
        print(f"  {st['duration']}−{st['cutTotal']}={st['finalDuration']}　成立：",
              abs(st["duration"]-st["cutTotal"]-st["finalDuration"])<1e-6)
        print("全部通過：", ok1 and ok3 and ok4 and ok5 and ok6 and ok7)
        print("例外：",len(c.exc))
        for e in c.exc: print("   ",e[:300])
asyncio.run(main())
