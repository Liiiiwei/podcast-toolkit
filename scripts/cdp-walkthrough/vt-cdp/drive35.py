import asyncio, json, hashlib, os
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
URL="http://127.0.0.1:8877/static/video-edit-prototype.html?demo=1"
SRT="/private/tmp/pt-e2e/20260825 端對端測試/03_成品/端對端測試_final_v2.srt"
def h(): return hashlib.md5(open(SRT,"rb").read()).hexdigest()[:8] if os.path.exists(SRT) else "無檔"

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))}); await asyncio.sleep(3.5)
        await c.ev("window.confirm=()=>true")   # demo 資料的確認框，測試裡一律同意

        print("【L0】版本指紋：兩顆送出鈕要在（不在＝來源沒同步，後面都不算數）")
        for _ in range(30):
            fp=await c.ev("[!!document.getElementById('vt-plan-push'),!!document.getElementById('vt-plan-render'),typeof window.__vtPlanPush]")
            if fp and fp[0] and fp[1] and fp[2]=="function": break
            await asyncio.sleep(0.5)
        print("  push鈕/render鈕/hook：",fp)
        if not(fp[0] and fp[1] and fp[2]=="function"): raise SystemExit("來源未同步，中止")
        await c.ev("window.confirm=()=>true")

        b=await c.ev("(()=>{const r=document.getElementById('vt-plan-open').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(b["x"],b["y"]); await asyncio.sleep(0.5)
        print("  面板開啟：",await c.ev("!document.getElementById('vt-plan').hidden"))

        print("【L1】JSON 壞掉 → 不送出，後端檔案零改動")
        before=h(); good=await c.ev("document.getElementById('vt-plan-json').value")
        await c.send("Runtime.evaluate",{"expression":"document.getElementById('vt-plan-json').value='{壞掉的'"})
        r=await c.ev("__vtPlanPush(false)")
        d=await c.ev("__vtPlanDialog()")
        print(f"  回傳 {r}　訊息「{d['msg'][:40]}」　色：{d['msgKind']}")
        print(f"  後端 SRT {before} → {h()}　零改動：", before==h())
        ok1 = r is None and "沒有送出" in d["msg"] and d["msgKind"]=="vt-plan-msg"

        print("【L2】只送不合成 → 後端真的寫進去了")
        await c.send("Runtime.evaluate",{"expression":"document.getElementById('vt-plan-json').value=%s"%json.dumps(good)})
        await c.ev("__vtAddCut(4.0,6.0)")   # 讓這次送出跟上一版有差
        await c.click(b["x"],b["y"]); await asyncio.sleep(0.4)   # 重開面板拿最新指令
        pre=h()
        r=await c.ev("__vtPlanPush(false)")
        d=await c.ev("__vtPlanDialog()")
        print("  回傳 applied：",(r or {}).get("applied"))
        print(f"  訊息「{d['msg']}」　色：{d['msgKind']}　鈕解鎖：{not d['busy']}")
        print(f"  後端 SRT {pre} → {h()}　真的變了：", pre!=h())
        ok2 = (r and r.get("ok") and r.get("assemble") is None and d["msgKind"]=="vt-plan-msg is-ok"
               and d["busy"] is False and pre!=h() and "已寫入" in d["msg"])

        print("【L3】送出中：三顆鈕都要鎖住（不鎖＝連按兩次送兩份指令）")
        await c.ev("""(()=>{const f=window.fetch;window.__origFetch=f;
          window.fetch=(...a)=>new Promise(res=>setTimeout(()=>res(f(...a)),1200))})()""")
        await c.ev("(()=>{window.__p=__vtPlanPush(false);return 1})()")
        await asyncio.sleep(0.4)
        mid=await c.ev("""(()=>({busy:document.getElementById('vt-plan-push').disabled,
          render:document.getElementById('vt-plan-render').disabled,
          apply:document.getElementById('vt-plan-apply').disabled,
          msg:document.getElementById('vt-plan-msg').textContent,
          kind:document.getElementById('vt-plan-msg').className}))()""")
        print("  送出中：",mid)
        await c.ev("window.__p"); await asyncio.sleep(0.3)
        post=await c.ev("__vtPlanDialog()")
        print(f"  結束後解鎖：{not post['busy']}　訊息「{post['msg'][:36]}」")
        ok3 = (mid["busy"] and mid["render"] and mid["apply"] and mid["kind"]=="vt-plan-msg is-busy"
               and "送出中" in mid["msg"] and post["busy"] is False)
        await c.ev("window.fetch=window.__origFetch")

        print("【L5】連不上後端 → 要講「連不上」而不是丟一串英文")
        await c.ev("window.fetch=()=>Promise.reject(new TypeError('Failed to fetch'))")
        r=await c.ev("__vtPlanPush(false)")
        d=await c.ev("__vtPlanDialog()")
        print(f"  回傳 {r}　訊息「{d['msg']}」")
        ok5 = r is None and "連不上後端" in d["msg"] and d["msgKind"]=="vt-plan-msg"
        await c.ev("window.fetch=window.__origFetch")

        print("【L4】送出並合成 → 真的開了 job")
        r=await c.ev("__vtPlanPush(true)")
        d=await c.ev("__vtPlanDialog()")
        print("  assemble：",(r or {}).get("assemble"))
        print(f"  訊息「{d['msg']}」")
        ok4 = r and r.get("assemble") and "已開始合成試看片" in d["msg"] and "本版未輸出" not in d["msg"]

        print("【L6】合成中再送 → 後端 409，訊息要是看得懂的中文")
        await asyncio.sleep(0.8)
        r=await c.ev("__vtPlanPush(false)")
        d=await c.ev("__vtPlanDialog()")
        print(f"  回傳 {r}　訊息「{d['msg']}」　色：{d['msgKind']}")
        ok6 = r is None and "合成" in d["msg"] and d["msgKind"]=="vt-plan-msg"
        await c.shot("L-送出面板")

        print("全部通過：", ok1 and ok2 and ok3 and ok4 and ok5 and ok6)
        print("  L1",ok1,"L2",ok2,"L3",ok3,"L4",bool(ok4),"L5",ok5,"L6",ok6)
        print("例外：",len(c.exc))
        for e in c.exc: print("   ",e[:300])
asyncio.run(main())
