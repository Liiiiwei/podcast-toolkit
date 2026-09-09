import asyncio, json
import websockets
exec(open("/private/tmp/vt-cdp/drive11.py").read().split("async def main()")[0])
URL="http://127.0.0.1:8877/static/video-edit-prototype.html?v=33"

R="__vtRender()"

# 攔 fetch：apply-plan 一律成功並宣稱開了合成；status 依序吐 __seq 裡的狀態
STUB = """(()=>{window.__of=window.__of||window.fetch;
  window.__seq=[]; window.__hit={cancel:0,reveal:0,body:null,revealPath:null,apply:0,confirm:0,confirmMsg:''};
  window.__ans=true; window.confirm=(m)=>{window.__hit.confirm++;window.__hit.confirmMsg=String(m);return window.__ans};
  window.fetch=(u,o)=>{const url=String(u);
    if(url.includes('/api/apply-plan')){window.__hit.apply++;
      try{window.__hit.body=JSON.parse(o.body)}catch(e){window.__hit.body=null}
      return Promise.resolve({ok:true,json:()=>Promise.resolve(
        {ok:true,applied:{cuts:1,subtitles:11,cards:2,empty_cards_skipped:0,style_keys:[]},
         assemble:{targets:['yt']}})})}
    if(url.includes('/api/assemble/cancel')){window.__hit.cancel++;
      window.__seq=[{state:'idle'}];
      return Promise.resolve({ok:true,json:()=>Promise.resolve({cancelled:true,state:'idle'})})}
    if(url.includes('/api/reveal')){window.__hit.reveal++;
      try{window.__hit.revealPath=JSON.parse(o.body).path}catch(e){window.__hit.revealPath=null}
      return Promise.resolve({ok:true,json:()=>Promise.resolve({ok:true})})}
    if(url.includes('/api/assemble/status')){
      const s=window.__seq.length>1?window.__seq.shift():window.__seq[0];
      if(s==='NET') return Promise.reject(new TypeError('Failed to fetch'));
      return Promise.resolve({ok:true,json:()=>Promise.resolve(s)})}
    return window.__of(u,o)}})()"""

def seq(*items): return "window.__seq=%s"%json.dumps(list(items))

# 走真 UI：頂列「輸出影片」才會帶 buildPlan()（__vtPlanPush 沒帶 override，讀的是空 textarea）
async def click_export(c):
    b=await c.ev("(()=>{const r=document.getElementById('vt-export').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
    await c.click(b["x"],b["y"])

async def wait_change(c, prev, timeout=8.0):
    """等 UI 文字從 prev 變成別的（poll 節奏與走查取樣本來就對不齊），逾時就回當下狀態讓斷言去紅"""
    t=0.0
    while t<timeout:
        s=await c.ev(R)
        if s["text"]!=prev: return s
        await asyncio.sleep(0.15); t+=0.15
    return await c.ev(R)

async def wait_text(c, sub, timeout=10.0):
    """等 UI 文字出現指定關鍵字，逾時就回當下狀態讓斷言去紅"""
    t=0.0
    while t<timeout:
        s=await c.ev(R)
        if sub in s["text"]: return s
        await asyncio.sleep(0.15); t+=0.15
    return await c.ev(R)

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
        else: raise SystemExit("__vtRender 不存在 —— 來源未同步，這輪不算數")

        print("【L0】版本指紋")
        print("  ", await c.ev("({hook:typeof window.__vtRender, 進度區:!!document.getElementById('vt-render'),"
                               "取消鈕:!!document.getElementById('vt-render-cancel'),"
                               "source:__vtPlan().source})"))

        print("【L1】empty 態：沒按輸出前，進度區整塊收起")
        e=await c.ev(R); print("  ",e)
        ok1 = e["hidden"] is True and e["exportDisabled"] is False and e["polling"] is False
        print("  L1 通過：", ok1)

        await c.ev(STUB)

        print("【L2】loading 態：preparing → running，進度條要動、鈕要鎖")
        await c.ev(seq({"state":"preparing"},
                       {"state":"running","percent":12.0,"eta_s":95,"index":0,"total":1},
                       {"state":"running","percent":63.4,"eta_s":30,"index":0,"total":1}))
        await click_export(c)
        a=await wait_change(c, ""); print("   preparing：",a)
        b=await wait_change(c, a["text"]); print("   running1 ：",b)
        d=await wait_change(c, b["text"]); print("   running2 ：",d)
        ok2 = (a["hidden"] is False and "準備合成影片" in a["text"] and a["cancelShown"] is True
               and a["exportDisabled"] is True and a["polling"] is True
               and "12%" in b["text"] and "1:35" in b["text"] and b["percent"]=="12%"
               and "63%" in d["text"] and d["percent"].startswith("63.4")
               and d["revealShown"] is False and d["closeShown"] is False)
        print(f"   ETA 換算 95s→1:35：{'1:35' in b['text']}　進度條寬度跟著走：{b['percent']}→{d['percent']}")
        full_body=await c.ev("window.__hit.body") or {}
        print("   整片輸出送出的 payload：",
              {k:full_body.get(k) for k in ("targets","force")},
              "　有沒有 preview_sec：", "preview_sec" in full_body)
        ok2 = ok2 and "preview_sec" not in full_body and a["previewDisabled"] is True
        print("  L2 通過：", ok2)
        await c.shot("L-合成中")

        print("【L3】success 態：done → 綠字、100%、檔名、Finder 鈕、停止 poll、解鎖")
        await c.ev(seq({"state":"done","percent":100.0,"eta_s":0,
                        "output_files":["/tmp/ep/03_成品/端對端測試_yt.mp4"]}))
        f=await wait_change(c, d["text"]); print("  ",f)
        rb=await c.ev("(()=>{const r=document.getElementById('vt-render-reveal').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(rb["x"],rb["y"]); await asyncio.sleep(0.4)
        hit=await c.ev("window.__hit"); print("   按「在 Finder 顯示」→ /api/reveal 被打：",hit["reveal"])
        ok3 = (f["kind"]=="vt-render is-ok" and f["percent"]=="100%" and "合成完成" in f["text"]
               and "端對端測試_yt.mp4" in f["text"] and f["revealShown"] is True
               and f["closeShown"] is True and f["cancelShown"] is False
               and f["polling"] is False and f["exportDisabled"] is False and hit["reveal"]==1)
        print("  L3 通過：", ok3)
        await c.shot("L-合成完成")

        print("【L4】收起：按 ✕ 進度區收回，狀態不殘留")
        cb=await c.ev("(()=>{const r=document.getElementById('vt-render-close').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()")
        await c.click(cb["x"],cb["y"]); await asyncio.sleep(0.3)
        g=await c.ev(R); print("  ",{k:g[k] for k in ("hidden","exportDisabled","polling")})
        ok4 = g["hidden"] is True and g["exportDisabled"] is False and g["polling"] is False
        print("  L4 通過：", ok4)

        print("【L5】error 態：合成失敗要看得見、不得畫成綠的")
        await c.ev(seq({"state":"preparing"},{"state":"error","error":"ffmpeg 掛了：codec 不支援"}))
        await click_export(c); await asyncio.sleep(2.0)
        h=await c.ev(R); print("  ",h)
        ok5 = (h["kind"]=="vt-render is-error" and "ffmpeg 掛了" in h["text"]
               and "is-ok" not in h["kind"] and h["polling"] is False
               and h["exportDisabled"] is False and h["cancelShown"] is False)
        print("  L5 通過：", ok5)
        await c.shot("L-合成失敗")

        print("【L6】取消：按取消 → 打 cancel API → 收回 idle，訊息是「已取消」不是「完成」")
        await c.ev(seq({"state":"running","percent":40.0,"eta_s":60,"index":0,"total":1}))
        await click_export(c); await asyncio.sleep(1.4)
        pre=await c.ev(R)
        # 取消鈕得真的看得見才點得到：display:none 的元素 rect 是 0，點下去只會打到頁面左上角
        xb=await c.ev("(()=>{const r=document.getElementById('vt-render-cancel').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2,w:r.width}})()")
        await c.click(xb["x"],xb["y"]); await asyncio.sleep(1.4)
        i=await c.ev(R); hit=await c.ev("window.__hit")
        print("   取消前：",pre["text"],"　取消鈕看得見：",pre["cancelShown"],"寬",round(xb["w"]))
        print("   取消後：",i,"　cancel API 次數：",hit["cancel"])
        ok6 = (pre["cancelShown"] is True and xb["w"]>0 and hit["cancel"]==1
               and i["text"]=="已取消合成" and "is-ok" not in i["kind"] and i["hidden"] is False
               and i["polling"] is False and i["exportDisabled"] is False and i["cancelShown"] is False)
        print("  L6 通過：", ok6)

        print("【L7】poll 連錯：網路瞬斷不判死，連三次才放棄且解鎖")
        await c.ev(seq("NET"))
        await click_export(c); await asyncio.sleep(1.2)
        j1=await c.ev(R)
        # poll 每秒一次，第 3 次失敗的落點會漂：等到它停下來為止，最多 8 秒
        for _ in range(40):
            j2=await c.ev(R)
            if j2["polling"] is False: break
            await asyncio.sleep(0.2)
        print("   第 1 次失敗後：", {k:j1[k] for k in ("text","polling","exportDisabled")})
        print("   連錯 3 次後 ：", {k:j2[k] for k in ("text","kind","polling","exportDisabled")})
        ok7 = (j1["polling"] is True and j1["exportDisabled"] is True
               and "看不到進度" in j2["text"] and j2["kind"]=="vt-render is-error"
               and j2["polling"] is False and j2["exportDisabled"] is False)
        print("  L7 通過：", ok7)

        print("【L8】試看 30 秒：送出帶 preview_sec=30、文字講「試看片」、兩顆出片鈕一起鎖")
        await c.ev("window.__hit.body=null")
        await c.ev(seq({"state":"running","percent":10.0,"eta_s":20,"index":0,"total":1}))
        pb=await c.ev("(()=>{const r=document.getElementById('vt-preview').getBoundingClientRect();"
                      "return{x:r.x+r.width/2,y:r.y+r.height/2,w:r.width,txt:document.getElementById('vt-preview').textContent.trim()}})()")
        await c.click(pb["x"],pb["y"])
        k=await wait_change(c, j2["text"])
        pv=await c.ev("window.__hit.body") or {}
        print("   試看鈕：", pv is not None and pb["txt"], "寬", round(pb["w"]))
        print("   送出的 payload：", {kk:pv.get(kk) for kk in ("targets","force","preview_sec")},
              "　plan 帶了當下時間軸：", isinstance(pv.get("plan"),dict) and "cards" in pv["plan"])
        print("   進度：", {kk:k[kk] for kk in ("text","hidden","exportDisabled","previewDisabled","polling")})
        ok8 = (pb["w"]>0 and pv.get("preview_sec")==30 and pv.get("force") is True
               and isinstance(pv.get("plan"),dict) and "cards" in pv["plan"]
               and "試看片" in k["text"] and k["hidden"] is False
               and k["exportDisabled"] is True and k["previewDisabled"] is True)
        print("  L8 通過：", ok8)
        await c.shot("L-試看合成中")

        print("【L9】輸出目標：勾了誰就出誰，一顆都沒勾要擋下來")
        await c.ev(seq({"state":"done","percent":100.0,"eta_s":0,"output_files":["/tmp/a.mp4"]}))
        await wait_change(c, k["text"])
        base=await c.ev(R)
        def CK(v): return ("(()=>{const e=document.querySelector('#vt-targets input[value=\"%s\"]');"
                           "const r=e.parentElement.getBoundingClientRect();"
                           "return{x:r.x+r.width/2,y:r.y+r.height/2,on:e.checked}})()") % v
        rl=await c.ev(CK("reels"))
        await c.click(rl["x"],rl["y"]); await asyncio.sleep(0.3)
        two=await c.ev(R)
        await c.ev("window.__hit.body=null")
        await c.ev(seq({"state":"running","percent":5.0,"eta_s":30,"index":0,"total":2}))
        await click_export(c); await wait_change(c, base["text"])
        multi=await c.ev("window.__hit.body") or {}
        print("   預設：", base["targets"], "→ 加勾 Reels：", two["targets"])
        # 鈕若被鎖住就根本沒送出，body 是 None——要讓斷言紅掉，不是讓走查炸掉
        print("   送出的 targets：", multi.get("targets"), "（None＝根本沒送出）")
        ok9a = base["targets"]==["yt"] and two["targets"]==["yt","reels"] and multi.get("targets")==["yt","reels"]

        # 全部取消勾：出片鈕要自己鎖上，而且是在「不忙」的狀態下鎖
        await c.ev(seq({"state":"done","percent":100.0,"eta_s":0,"output_files":["/tmp/a.mp4"]}))
        for _ in range(40):
            if (await c.ev(R))["polling"] is False: break
            await asyncio.sleep(0.2)
        for v in ("yt","reels"):
            b=await c.ev(CK(v))
            if b["on"]: await c.click(b["x"],b["y"]); await asyncio.sleep(0.25)
        z=await c.ev(R)
        # 讀頂列那顆——對話框沒開時，使用者只看得到這一個
        msg=await c.ev("document.getElementById('vt-export-msg').textContent")
        red=await c.ev("document.getElementById('vt-targets').classList.contains('is-empty')")
        print("   全部取消勾：", z["targets"], "　出片鈕鎖住：",
              z["exportDisabled"], z["previewDisabled"], "　外框標紅：", red)
        print("   提示：", msg)
        ok9b = (z["targets"]==[] and z["exportDisabled"] is True and z["previewDisabled"] is True
                and z["polling"] is False and red is True and "至少要選一個輸出目標" in msg)

        # 勾回來要能解鎖，不然使用者就卡死了
        b=await c.ev(CK("yt")); await c.click(b["x"],b["y"]); await asyncio.sleep(0.3)
        r9=await c.ev(R)
        print("   勾回 YT：", r9["targets"], "　解鎖：", r9["exportDisabled"] is False)
        ok9 = ok9a and ok9b and r9["targets"]==["yt"] and r9["exportDisabled"] is False and r9["previewDisabled"] is False
        print("  L9 通過：", ok9)
        await c.shot("L-輸出目標")

        print("【L10】多目標成品：文字報數量、完整清單掛 title、Finder 開第一個（不是最後那個 mp3）")
        await c.ev("window.__hit.revealPath=null")
        await c.ev(seq({"state":"running","percent":5.0,"eta_s":30,"index":0,"total":3},
                       {"state":"done","percent":100.0,"eta_s":0,
                        "output_files":["/out/ep.mp4","/out/ep_reels.mp4","/out/ep.mp3"]}))
        await click_export(c)
        await wait_text(c, "個檔案")
        info=await c.ev("(()=>{const e=document.getElementById('vt-render-text');"
                        "return{txt:e.textContent,title:e.title,"
                        "clipped:e.scrollWidth>e.clientWidth+1}})()")
        rb=await c.ev("(()=>{const r=document.getElementById('vt-render-reveal').getBoundingClientRect();"
                      "return{x:r.x+r.width/2,y:r.y+r.height/2,w:r.width}})()")
        await c.click(rb["x"],rb["y"]); await asyncio.sleep(0.4)
        rp=await c.ev("window.__hit.revealPath")
        print("   完成文字：", info["txt"], "　title 行數：", info["title"].count("\n")+1,
              "　被擠到截斷：", info["clipped"])
        print("   Finder 開的是：", rp)
        ok10 = ("3 個檔案" in info["txt"] and info["clipped"] is False
                and info["title"].count("\n")==2
                and "ep.mp3" in info["title"] and "ep.mp4" in info["title"]
                and rb["w"]>0 and rp=="/out/ep.mp4")
        print("  L10 通過：", ok10)
        await c.shot("L-多目標成品")

        print("【L11】出片前合理性檢查：卡被剪掉要先問、剪光了要擋住")
        await c.ev(seq({"state":"idle"}))
        # 造一張卡，再剪掉它所在的整段 —— 這張卡出不到成品裡
        made=await c.ev("!!__vtDropTpl('big', 5.0)")   # 版型代號錯的話它靜默回 null
        await c.ev("__vtAddCut(4.0, 9.0)")
        drop=await c.ev("(()=>{const p=__vtPlan();return{cards:p.cards.length,"
                        "dropped:p.cards.filter(c=>c.dropped).length,fin:p.finalDuration}})()")
        await c.ev("window.__ans=false; window.__hit.apply=0; window.__hit.confirm=0")
        await click_export(c); await asyncio.sleep(0.5)
        h1=await c.ev("({apply:__hit.apply,confirm:__hit.confirm,msg:__hit.confirmMsg})")
        st1=await c.ev(R)
        print("   卡/被剪掉/剪後長度：", drop)
        print("   按取消 → 送出次數：", h1["apply"], "　問過：", h1["confirm"], "　鈕還能按：", st1["exportDisabled"] is False)
        print("   問句：", h1["msg"])
        ok11a = (made is True and drop["dropped"]==1 and h1["confirm"]==1 and h1["apply"]==0
                 and "不會出現在成品中" in h1["msg"] and st1["exportDisabled"] is False)

        await c.ev("window.__ans=true; window.__hit.apply=0")
        await click_export(c); await asyncio.sleep(0.5)
        h2=await c.ev("__hit.apply")
        print("   按確定 → 送出次數：", h2)
        ok11b = (h2==1)

        # 全部剪光：連問都不問，直接擋
        await c.ev(seq({"state":"idle"}))
        await c.ev("__vtAddCut(0, __vt.duration)")
        await c.ev("window.__hit.apply=0; window.__hit.confirm=0")
        fin=await c.ev("__vtPlan().finalDuration")
        await click_export(c); await asyncio.sleep(0.5)
        h3=await c.ev("({apply:__hit.apply,confirm:__hit.confirm})")
        msg=await c.ev("(()=>{const e=document.getElementById('vt-export-msg');"
                       "return{txt:e.textContent,cls:e.className}})()")
        st3=await c.ev(R)
        print("   剪光後長度：", fin, "　送出次數：", h3["apply"], "　問過：", h3["confirm"])
        print("   訊息：", msg["txt"], "｜鈕還能按：", st3["exportDisabled"] is False)
        ok11c = (fin==0 and h3["apply"]==0 and h3["confirm"]==0
                 and "剪後長度是 0" in msg["txt"] and st3["exportDisabled"] is False)
        ok11 = ok11a and ok11b and ok11c
        print("  L11 通過：", ok11, "（問過才送=%s／確定就送=%s／剪光擋住=%s）"%(ok11a,ok11b,ok11c))
        await c.shot("L-剪光擋住")

        print("【L12】卡落在剪除區時，軌上就看得出來（不必等按下輸出才被問）")
        # 重載清掉 L11 剪光的狀態；這關不送出，不需要 STUB
        await c.send("Page.navigate",{"url":URL+"&l12=1"})
        for _ in range(60):
            if await c.ev("typeof window.__vtDropTpl")=="function": break
            await asyncio.sleep(0.5)
        else: raise SystemExit("L12 重載失敗")
        for _ in range(40):                      # 影片 metadata 沒好時 duration=0，卡會被夾成 0 長
            if await c.ev("__vt.duration>0"): break
            await asyncio.sleep(0.25)
        # 卡長預設 3 秒：2.0 全留、11.0 全在剪除區、13.0 跨在邊界上
        made=[await c.ev("!!__vtDropTpl('big', %s)"%t) for t in (2.0, 11.0, 13.0)]
        print("   duration：", await c.ev("__vt.duration"), "　建卡：", made,
              "　DOM 卡數：", await c.ev("document.querySelectorAll('#vt-card-track .vt-card').length"))
        await c.ev("__vtAddCut(10.0, 14.0)")
        cards=await c.ev("""[...document.querySelectorAll('#vt-card-track .vt-card')].map(e=>{
          const s=getComputedStyle(e);
          return {cls:e.className, op:+s.opacity, border:s.borderTopStyle,
            line:getComputedStyle(e.querySelector('.vt-card-txt')).textDecorationLine,
            tip:(e.title.split('\\n')[2]||'')};
        })""")
        for c_ in (cards or []): print("   ", c_)
        cut =[x for x in cards if "is-cut" in x["cls"]]
        part=[x for x in cards if "is-partial" in x["cls"]]
        kept=[x for x in cards if not ("is-cut" in x["cls"] or "is-partial" in x["cls"])]
        ok12 = (len(cards)==3 and len(kept)==1 and len(cut)==1 and len(part)==1
                and kept[0]["op"]==1 and kept[0]["tip"].startswith("點=")
                # CSS 真的生效才算數：壓暗、虛線框、文字劃掉
                and cut[0]["op"]<0.5 and cut[0]["border"]=="dashed"
                and cut[0]["line"]=="line-through"
                and "不會出現在成品裡" in cut[0]["tip"]
                and part[0]["op"]==1 and part[0]["border"]=="dashed"
                and "會變短" in part[0]["tip"])
        print("  L12 通過：", ok12)
        await c.shot("L-卡被剪掉看得出來")

        print("全部通過：", ok1 and ok2 and ok3 and ok4 and ok5 and ok6 and ok7 and ok8 and ok9 and ok10 and ok11 and ok12)
        print("例外：",len(c.exc))
        for e in c.exc: print("   ",e[:300])
asyncio.run(main())
