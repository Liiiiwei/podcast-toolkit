import asyncio, json, hashlib, pathlib
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
URL="http://127.0.0.1:8877/static/video-edit-prototype.html?demo=1"
EP=pathlib.Path("/private/tmp/pt-e2e/20260825 端對端測試")

MSG="""(()=>{const e=document.getElementById('vt-export-msg');
  return {cls:e.className, text:e.textContent,
          exportOff:document.getElementById('vt-export').disabled,
          panelOpen:!document.getElementById('vt-plan').hidden}})()"""

async def clickBtn(c, bid):
    r=await c.ev(f"(()=>{{const r=document.getElementById('{bid}').getBoundingClientRect();return{{x:r.x+r.width/2,y:r.y+r.height/2}}}})()")
    await c.click(r["x"],r["y"])

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.addScriptToEvaluateOnNewDocument",{"source":"window.confirm=()=>true"})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))})

        print("【E0】版本指紋：頂列要有「輸出影片」主鈕")
        for _ in range(30):
            fp=await c.ev("""(()=>{const b=document.getElementById('vt-export');
              return b?[b.textContent.trim(), b.className, !!document.getElementById('vt-export-msg'),
                        document.getElementById('vt-plan-open').textContent.trim()]:null})()""")
            if fp and fp[2]: break
            await asyncio.sleep(0.5)
        print("  ", fp)
        if not fp or fp[0]!="輸出影片" or "is-primary" not in fp[1]:
            raise SystemExit("來源未同步，中止")
        e0 = fp[0]=="輸出影片" and fp[3]=="剪輯指令…"

        for _ in range(60):
            r=await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState,v.seekable.length]})()")
            if r and r[0]>=1 and r[1]>0: break
            await asyncio.sleep(0.5)
        print("  video readyState/seekable：",r)

        print("【E1】面板從沒開過，主鈕也要能送出當下時間軸（不靠 textarea）")
        await c.ev("__vtAddCut(4.0,6.0)"); await c.ev("__vtAddCut(12.0,13.5)"); await asyncio.sleep(0.3)
        await c.ev("__vtPromote(1)"); await asyncio.sleep(0.3)
        pre=await c.ev(MSG)
        ta=await c.ev("document.getElementById('vt-plan-json').value.length")
        print(f"  面板開過嗎：{pre['panelOpen']}　textarea 長度：{ta}（0＝從沒填過）")

        print("【E2】loading：按下瞬間主鈕鎖住、頂列顯示送出中")
        await c.ev("(()=>{const f=window.fetch.bind(window);window.fetch=(...a)=>new Promise(r=>setTimeout(()=>r(f(...a)),700))})()")
        await clickBtn(c,"vt-export"); await asyncio.sleep(0.25)
        busy=await c.ev(MSG)
        print("  ", busy)
        e2 = busy["exportOff"] is True and "is-busy" in busy["cls"] and "送出並合成中" in busy["text"]
        print("  E2 通過：", e2)
        await c.shot("E-頂列輸出中")

        print("【E3】success：綠字＋真的開始合成完整影片")
        for _ in range(40):
            ok=await c.ev(MSG)
            if not ok["exportOff"]: break
            await asyncio.sleep(0.5)
        print("  ", ok)
        e3 = "is-ok" in ok["cls"] and "已開始合成影片" in ok["text"] and "剪除 2 段" in ok["text"]
        print("  E3 通過：", e3)
        await c.shot("E-頂列輸出成功")

        y=__import__("yaml").safe_load((EP/"episode.yaml").read_text(encoding="utf-8"))
        print("  episode.yaml cuts：", y.get("cuts"))
        e3b = y.get("cuts")==[[4.0,6.0],[12.0,13.5]]
        print("  E3b 副作用落地：", e3b)

        print("【E4】合成還在跑的時候，重複送出要送不出去")
        # 舊斷言等的是「按下去被文字擋下來」；現在主鈕在合成期間就已經 disabled，
        # 根本按不到那條路徑 —— 防重複送出的位置往前移了，走查跟著改斷言，不改產品碼。
        before=await c.ev(MSG)
        await clickBtn(c,"vt-export"); await asyncio.sleep(1.4)
        err=await c.ev(MSG)
        print("  ", err)
        e4 = (err["exportOff"] is True                 # 鈕還鎖著＝點不動
              and err["text"]==before["text"]          # 訊息沒變＝沒有第二次送出
              and "is-err" not in err["cls"])
        print("  E4 通過：", e4)
        await c.shot("E-合成中重複送出被擋")

        print("【E5】JSON 面板降級後仍可用（除錯入口沒壞）")
        await clickBtn(c,"vt-plan-open"); await asyncio.sleep(0.5)
        d=await c.ev("__vtPlanDialog()")
        print("  面板開啟：",d["open"],"　JSON 字數：",d["len"])
        e5 = d["open"] is True and d["len"]>200
        print("  E5 通過：", e5)

        print("全部通過：", e0 and e2 and e3 and e3b and e4 and e5)
        print("例外：",len(c.exc))
        for e in c.exc: print("   ",e[:300])
asyncio.run(main())
