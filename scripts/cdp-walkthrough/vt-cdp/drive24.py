import asyncio, json, urllib.request, base64
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])

async def main():
    page=[t for t in hj("/json/list") if t["type"]=="page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"],max_size=90_000_000) as ws:
        c=CDP(ws)
        for d in ("Page.enable","Runtime.enable","Network.enable"): await c.send(d)
        await c.send("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1000,"deviceScaleFactor":2,"mobile":False})
        await c.send("Network.setCacheDisabled",{"cacheDisabled":True})
        await c.send("Page.navigate",{"url":URL+"&nocache="+str(id(c))}); await asyncio.sleep(3.5)
        for _ in range(30):
            st=await c.ev("(()=>{const v=document.getElementById('vt-video');return [v.readyState, v.seekable.length]})()")
            if st and st[0]>=1 and st[1]>0: break
            await asyncio.sleep(0.4)
        print("video readyState/seekable：", st)
        print("【V0】版本指紋 __vtCardViews：", await c.ev("typeof window.__vtCardViews"))

        # 兩張卡：第3句升格（big），再在它中間丟一張下標條 → 必然重疊
        a=await c.ev("__vtPromote(2)"); await asyncio.sleep(0.2)
        mid=(a["start"]+a["end"])/2
        b=await c.ev(f"__vtDropTpl('lower', {mid:.3f})"); await asyncio.sleep(0.3)
        print(f"【V1】卡A big {a['start']:.2f}–{a['end']:.2f}　卡B lower {b['start']:.2f}–{b['end']:.2f}")
        ov_s, ov_e = max(a["start"],b["start"]), min(a["end"],b["end"])
        print(f"     重疊區 {ov_s:.2f}–{ov_e:.2f}（有重疊 {ov_e>ov_s}）")

        async def views(t):
            await c.ev(f"__vtSeek({t:.3f})")
            for _ in range(15):
                await asyncio.sleep(0.15)
                v=await c.ev("__vtCardViews()")
                if v is not None: return v
            return []
        tmid=(ov_s+ov_e)/2
        v=await views(tmid)
        print(f"【V2】播放頭 {tmid:.2f}s（重疊區）→ 疊層張數 {len(v)}（期待 2）",
              [(x["id"], x["cls"].split()[1]) for x in v])
        print("     兩張都在：", len(v)==2 and {x['id'] for x in v}=={a['id'],b['id']})

        # 選被壓在下面的那張（DOM 第一張）
        under=v[0]["id"] if v else None
        await c.ev(f"__vtSelectCard({under})"); await asyncio.sleep(0.4)
        v=await c.ev("__vtCardViews()")
        sel=[x for x in v if x["selected"]]
        print(f"【V3】選取下層卡 id={under} → 疊層仍 {len(v)} 張，selected={[x['id'] for x in sel]}，z={sel[0]['z'] if sel else None}")
        print("     選了看得見且浮到上層：", len(sel)==1 and sel[0]["id"]==under and sel[0]["z"]=="2")

        # 拖被選取的那張：只有它動
        before={x["id"]:x for x in await c.ev("__vtCards()")}
        box=await c.ev("(()=>{const r=document.getElementById('vt-card-layer').getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height}})()")
        pos=await c.ev(f"(()=>{{const e=[...document.querySelectorAll('#vt-card-layer .vt-card-view')].find(n=>+n.dataset.id==={under});const r=e.getBoundingClientRect();return{{x:r.x+r.width/2,y:r.y+r.height/2}}}})()")
        await c.drag(pos["x"],pos["y"], box["x"]+box["w"]*0.30, box["y"]+box["h"]*0.22, steps=10)
        await asyncio.sleep(0.4)
        after={x["id"]:x for x in await c.ev("__vtCards()")}
        other=[i for i in after if i!=under][0]
        print(f"【V4】拖下層卡 → id={under} x {before[under]['x']:.3f}→{after[under]['x']:.3f} y {before[under]['y']:.3f}→{after[under]['y']:.3f}")
        print(f"     另一張沒被動到：", abs(after[other]['x']-before[other]['x'])<1e-9 and abs(after[other]['y']-before[other]['y'])<1e-9)
        print(f"     真的移動了：", abs(after[under]['x']-0.30)<0.03 and abs(after[under]['y']-0.22)<0.03)
        await c.shot("V-重疊兩張卡")

        # 只有一張的區段 / 沒有卡的區段
        only = (a["start"]+min(b["start"],a["end"]))/2 if b["start"]>a["start"] else None
        t1 = a["start"]+0.05 if b["start"]>a["start"]+0.1 else b["end"]+0.05
        v1=await views(t1)
        print(f"【V5】播放頭 {t1:.2f}s（只命中一張）→ 張數 {len(v1)}（期待 1）")
        t0=max(a["end"],b["end"])+2
        v0=await views(t0)
        print(f"【V6】播放頭 {t0:.2f}s（都沒命中）→ 張數 {len(v0)}（期待 0）")

        # 切換選取要立刻反映（快取指紋含 selected）
        await c.ev(f"__vtSeek({tmid:.3f})"); await asyncio.sleep(0.4)
        await c.ev(f"__vtSelectCard({other})"); await asyncio.sleep(0.4)
        v=await c.ev("__vtCardViews()")
        sel=[x["id"] for x in v if x["selected"]]
        print(f"【V7】改選另一張 → selected={sel}（期待 [{other}]），張數 {len(v)}")

        st=await c.ev("__vtStats()")
        print(f"【V8】不變式 {st['duration']}−{st['cutTotal']}={st['finalDuration']}：",
              abs(st['duration']-st['cutTotal']-st['finalDuration'])<1e-6, "　卡數", len(await c.ev("__vtCards()")))
        print("例外：", len(c.exc))
        for e in c.exc: print("  ", e[:400])
asyncio.run(main())
