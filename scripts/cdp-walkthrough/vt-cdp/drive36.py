import asyncio, json, os
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
URL="http://127.0.0.1:8877/static/video-edit-prototype.html?demo=1"
YML="/private/tmp/pt-e2e/20260825 端對端測試/episode.yaml"

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))})
        for _ in range(30):
            await asyncio.sleep(0.5)
            if await c.ev("typeof window.__vtPlanPush")=="function": break
        else: raise SystemExit("來源未同步")
        await c.ev("window.confirm=()=>true")

        print("【M1】做一輪真剪輯：剪 2 段 + 升格 1 張標題卡")
        await c.ev("__vtAddCut(4.0,6.0)"); await c.ev("__vtAddCut(12.0,13.5)")
        await c.ev("__vtPromote(2)"); await asyncio.sleep(0.5)
        st=await c.ev("__vtStats()")
        print(f"  原長 {st['duration']}−剪除 {st['cutTotal']}={st['finalDuration']}　卡數 {len(await c.ev('__vtCards()'))}")

        print("【M2】點「輸出剪輯指令」→ textarea 要含這一輪的剪除")
        b=await c.ev("(()=>{const r=document.getElementById('vt-plan-open').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(b["x"],b["y"]); await asyncio.sleep(0.6)
        ta=await c.ev("(()=>{const p=JSON.parse(document.getElementById('vt-plan-json').value);return{cuts:p.cuts,cards:p.cards.length,subs:p.subtitles.length}})()")
        print("  指令內容：",ta)
        ok2 = ta["cuts"]==[[4,6],[12,13.5]] and ta["cards"]==1

        print("【M3】用真滑鼠點「送到這一集」（不是 hook）")
        pre=open(YML).read()
        # 本機後端太快，0.2 秒就回來了 —— 把 fetch 拖慢 600ms 才看得到 busy 態
        await c.ev("""(()=>{const f=window.fetch;window.__of=f;
          window.fetch=(...a)=>new Promise(r=>setTimeout(()=>r(f(...a)),600))})()""")
        pb=await c.ev("(()=>{const r=document.getElementById('vt-plan-push').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(pb["x"],pb["y"])
        await asyncio.sleep(0.25)
        mid=await c.ev("__vtPlanDialog()")
        for _ in range(40):
            d=await c.ev("__vtPlanDialog()")
            if not d["busy"]: break
            await asyncio.sleep(0.3)
        print(f"  按下瞬間 busy={mid['busy']}　色={mid['msgKind']}")
        print(f"  完成後：「{d['msg']}」　色={d['msgKind']}　鈕解鎖={not d['busy']}")
        await c.shot("M-真滑鼠送出成功")

        print("【M4】後端副作用：cuts 進 episode.yaml")
        post=open(YML).read()
        import yaml
        y=yaml.safe_load(post)
        print("  yaml 的 cuts：", y.get("cuts"))
        print("  yaml 真的變了：", pre!=post)
        keep=[k for k in ("date","name","fixes","speed","main_video") if k in y]
        print("  原有欄位還在：", keep)
        print("  樣式也在：", {k:y["subtitle_style"][k] for k in ("font_size","margin_v","primary_colour")})
        ok4 = y.get("cuts")==[[4.0,6.0],[12.0,13.5]] and len(keep)==5

        print("【M5】標題卡本版不輸出，要明說（不能靜靜吃掉）")
        ok5 = "標題卡 1 張本版未輸出" in d["msg"]
        print("  訊息含提醒：", ok5)

        print("全部通過：", bool(ok2 and d["msgKind"]=="vt-plan-msg is-ok" and mid["busy"] and ok4 and ok5))
        print("  M2",ok2,"M3",mid["busy"] and d["msgKind"]=="vt-plan-msg is-ok","M4",bool(ok4),"M5",ok5)
        await c.ev("window.fetch=window.__of")
        print("例外：",len(c.exc))
        for e in c.exc: print("   ",e[:300])
asyncio.run(main())
