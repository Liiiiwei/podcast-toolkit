import asyncio, json, os, glob, time
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
URL="http://127.0.0.1:8877/static/video-edit-prototype.html?v=34"
R="__vtRender()"
OUT="/private/tmp/pt-e2e/20260825 端對端測試/03_成品"

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))})
        for _ in range(60):
            if await c.ev("typeof window.__vtRender")=="function": break
            await asyncio.sleep(0.5)
        else: raise SystemExit("__vtRender 不存在")
        for _ in range(60):
            r=await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState,v.seekable.length]})()")
            if r and r[0]>=1 and r[1]>0: break
            await asyncio.sleep(0.5)
        print("【M0】真後端、真 ffmpeg。video：",r,"　source：",await c.ev("__vtPlan().source"))

        # 同名成品會被覆寫，所以「有沒有新產出」只能看 mtime，不能看檔名差集
        t0=time.time()
        before={os.path.basename(x): os.path.getmtime(x) for x in glob.glob(OUT+"/*.mp4")}
        print("  合成前成品：", {k: round(t0-v,1) for k,v in before.items()} or "（無）", "（值＝幾秒前寫的）")

        print("【M1】造一點剪輯：剪除 1 段 + 升格 1 張標題卡，然後按「輸出影片」")
        await c.ev("__vtAddCut(4.0,6.0)"); await c.ev("__vtPromote(2)"); await asyncio.sleep(0.5)
        b=await c.ev("(()=>{const r=document.getElementById('vt-export').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(b["x"],b["y"])

        print("【M2】盯 UI 的進度變化（每 2 秒抓一次，最多 240 秒）")
        seen=[]; last=None; done=None
        for k in range(120):
            await asyncio.sleep(2.0)
            s=await c.ev(R)
            key=(s["kind"], s["text"])
            if key!=last:
                seen.append((round(k*2+2,0), s["text"], s["percent"], s["exportDisabled"], s["polling"]))
                last=key
                print(f"   +{k*2+2:>3}s　{s['text'][:46]:<46} bar={s['percent']:<7} 鎖鈕={s['exportDisabled']} poll={s['polling']}")
            if s["polling"] is False and s["hidden"] is False and "準備" not in s["text"]:
                done=s; break
        if done is None: raise SystemExit("240 秒沒收斂 —— 合成太慢或卡住")

        print("【M3】終態驗收")
        print("  ", done)
        fresh=[(os.path.basename(x), os.path.getmtime(x), os.path.getsize(x))
               for x in glob.glob(OUT+"/*.mp4") if os.path.getmtime(x)>t0]
        print("  這次跑出來的成品檔（mtime 晚於走查起點）：",
              [(n, f"{sz/1024/1024:.1f} MB") for n,_,sz in fresh] or "（無）")
        runningseen=[x for x in seen if "合成影片中" in x[1]]
        okA = done["kind"]=="vt-render is-ok" and "合成完成" in done["text"]
        okB = done["exportDisabled"] is False and done["revealShown"] is True and done["cancelShown"] is False
        okC = len(fresh)==1 and fresh[0][0] in done["text"] and fresh[0][2]>1_000_000
        okD = len(runningseen)>=1 and any(x[3] is True for x in runningseen)
        print(f"  UI 顯示完成：{okA}　鈕解鎖且可開資料夾：{okB}")
        print(f"  檔名與畫面上寫的一致、且不是空檔：{okC}" + (f"（{fresh[0][2]/1024/1024:.1f} MB）" if fresh else "（沒有新檔）"))
        print(f"  中途真的看到「合成中 N%」且當下鈕是鎖的：{okD}（{len(runningseen)} 次變化）")
        print("全部通過：", okA and okB and okC and okD)
        print("例外：",len(c.exc))
        for e in c.exc: print("   ",e[:300])
        await c.shot("M-真合成完成")
asyncio.run(main())
