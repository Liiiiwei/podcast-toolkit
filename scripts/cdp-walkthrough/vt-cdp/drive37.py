import asyncio, hashlib, os
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
URL="http://127.0.0.1:8877/static/video-edit-prototype.html?demo=1"
SRT="/private/tmp/pt-e2e/20260825 端對端測試/03_成品/端對端測試_final_v2.srt"
def h(): return hashlib.md5(open(SRT,"rb").read()).hexdigest()[:8]
async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page","Runtime","Network"): await c.send(d+".enable")
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))})
        for _ in range(30):
            await asyncio.sleep(0.5)
            if await c.ev("typeof window.__vtPlanPush")=="function": break
        else: raise SystemExit("來源未同步")
        await c.ev("window.confirm=()=>true")
        await c.ev("(()=>{document.getElementById('vt-plan').hidden=false;document.getElementById('vt-plan-json').value='{壞掉的'})()")
        before=h()
        r=await c.ev("__vtPlanPush(false)")
        d=await c.ev("__vtPlanDialog()")
        ok = r is None and "沒有送出" in d["msg"] and before==h()
        print(f"  壞 JSON → 回傳 {r}　訊息「{d['msg'][:36]}」")
        print(f"  後端 SRT {before} → {h()}")
        print("  N3 斷言（不送出且檔案零改動）：", ok)
asyncio.run(main())
