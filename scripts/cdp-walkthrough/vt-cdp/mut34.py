import subprocess, shutil, re
S="/Users/Mac365/conductor/workspaces/podcast-toolkit/colombo/podcast_toolkit/web/static/"
JS=S+"video-edit-prototype.js"; HTML=S+"video-edit-prototype.html"
BAK_JS="/private/tmp/vtjs_before_mut.js"; BAK_HTML="/private/tmp/vthtml_before_mut.html"

# (代號, 要關掉的部件說明, 檔案, 原文, 改成什麼, 預期被哪一項抓到)
MUTS=[
 ("N1","合成結束後解鎖按鈕",JS,
  "    renderTimer = null;\n    setPlanButtonsDisabled(false);","    renderTimer = null;","L3/L5/L6 exportDisabled"),
 ("N2","合成期間鎖住按鈕",JS,
  "    setPlanButtonsDisabled(true);\n    paintRender({ text: `準備合成","    paintRender({ text: `準備合成","L2 exportDisabled"),
 ("N3","連錯三次才放棄的門檻",JS,
  "      if (renderFails >= 3) {","      if (renderFails >= 1) {","L7 第一次失敗就死"),
 ("N4","取消後不畫成成功",JS,
  '    paintRender({ text: "已取消合成" });','    paintRender({ kind: "is-ok", percent: 100, text: "已取消合成" });',"L6 kind"),
 ("N5","記下成品路徑（Finder 鈕）",JS,
  "      renderOutputs = s.output_files || [];","      renderOutputs = [];","L3 revealShown"),
 ("N6","送出成功後啟動盯進度",JS,
  "      if (body.assemble) {","      if (false) {","L2/L3 整串不動"),
 ("N8","試看鈕只出前 30 秒（preview_sec）",JS,
  '                ...(render === "preview" ? { preview_sec: 30 } : {}),',"",
  "L8 payload 沒帶 preview_sec"),
 ("N9","合成期間連試看鈕一起鎖",JS,
  '      "vt-export",\n      "vt-preview",\n','      "vt-export",\n',
  "L2/L8 previewDisabled"),
 ("N10","進度文字分辨試看片與整片",JS,
  'startRenderWatch(render === "preview" ? "試看片" : "影片");','startRenderWatch("影片");',
  "L8 文字沒寫試看片"),
 ("N11","送出用勾選的目標（而非寫死 yt）",JS,
  "                targets: readTargets(),",'                targets: ["yt"],',
  "L9 送出的 targets 不跟著勾選走"),
 ("N12","沒選目標時擋住出片鈕",JS,
  "      if (el) el.disabled = planBusy || none;","      if (el) el.disabled = planBusy;",
  "L9 全部取消勾後鈕沒鎖"),
 ("N13","多檔時報出檔案數量（而非只報一個檔名）",JS,
  """        text: !names.length
          ? "合成完成"
          : names.length === 1
            ? `合成完成・${names[0]}`
            : `合成完成・${names.length} 個檔案`,""",
  '        text: names.length ? `合成完成・${names[names.length - 1]}` : "合成完成",',
  "L10 文字沒寫『3 個檔案』"),
 ("N14","Finder 開第一個檔（影片）而非最後一個（mp3）",JS,
  "        body: JSON.stringify({ path: renderOutputs[0] }),",
  "        body: JSON.stringify({ path: renderOutputs[renderOutputs.length - 1] }),",
  "L10 Finder 開到 ep.mp3"),
 ("N15","剪光了就擋住不送去合成",JS,
  '    if (typeof plan.finalDuration === "number" && plan.finalDuration <= 0) {\n      planMsg("", "剪後長度是 0，沒有東西可以合成");\n      return false;\n    }\n',"",
  "L11 剪光後仍送出"),
 ("N16","卡被剪掉時先問過使用者",JS,
  '    const lost = (plan.cards || []).filter((c) => c.dropped).length;\n    if (lost) {\n      return confirm(\n        `有 ${lost} 張標題卡落在剪掉的段落裡，不會出現在成品中。仍要合成嗎？`,\n      );\n    }\n',"",
  "L11 沒問就直接送出"),
 ("N17","卡被剪掉時在軌上換樣式（class）",JS,
  '      el.className =\n        "vt-card" +\n        (state.selectedCard === c.id ? " is-selected" : "") +\n        (cut === "cut" ? " is-cut" : cut === "partial" ? " is-partial" : "");\n',
  '      el.className =\n        "vt-card" + (state.selectedCard === c.id ? " is-selected" : "");\n',
  "L12 三張卡樣式都一樣"),
 ("N18","讓 is-cut／is-partial 真的有視覺差異的那幾條 CSS",HTML,
  '      /* 落在剪除區：這張卡不會出現在成品裡，在軌上就講清楚（比照 .vt-sub.is-cut）*/\n      .vt-card.is-cut {\n        opacity: 0.35;\n        background: transparent;\n        border-style: dashed;\n        border-color: var(--border-strong);\n      }\n      .vt-card.is-cut .vt-card-txt {\n        color: var(--text-faint);\n        text-decoration: line-through;\n      }\n      /* 跨在剪除邊界：成品裡這張卡會變短 */\n      .vt-card.is-partial {\n        border-style: dashed;\n        border-color: var(--warning);\n      }\n', "",
  "L12 opacity/虛線框/劃掉沒生效"),
 ("N19","hover 說明講清楚這張卡出不到成品",JS,
  '      (st === "cut"\n        ? "這段被剪掉了，這張卡不會出現在成品裡\\n"\n        : st === "partial"\n          ? "有一段被剪掉，這張卡在成品裡會變短\\n"\n          : "") +\n', '      "" +\n',
  "L12 tip 沒有那兩句"),
 ("N7","讓 hidden 真的隱藏的那條 CSS",HTML,
  "      .vt-render[hidden],\n      .vt-render-btn[hidden] {\n        display: none;\n      }\n","",
  "L1 進度區其實沒收起／L3 取消鈕沒消失"),
]

def run():
    r=subprocess.run(["/private/tmp/pt-venv/bin/python","-u","drive33.py"],
                     capture_output=True,text=True,cwd="/private/tmp/vt-cdp",timeout=300)
    o=r.stdout+r.stderr
    m=re.search(r"全部通過： (True|False)",o)
    return (m.group(1)=="True" if m else None), o

def restore():
    shutil.copy(BAK_JS,JS); shutil.copy(BAK_HTML,HTML)

restore()
base,o=run()
if base is not True:
    print("【基準】第一次不綠，重試一次。失敗段落：")
    print("\n".join([l for l in o.split("\n") if "通過： False" in l]))
    base,o=run()
print("【基準】未突變 → 全部通過：",base)
if base is not True:
    print("基準不綠，突變測試沒有意義，中止"); raise SystemExit(1)

caught={}
for code,desc,path,old,new,expect in MUTS:
    restore()
    s=open(path).read()
    if s.count(old)!=1:
        print(f"{code} 錨點命中 {s.count(old)} 次 —— 跳過（要改錨點）"); caught[code]=None; continue
    open(path,"w").write(s.replace(old,new))
    ok,out=run()
    # ok 為 None＝走查沒印出結論（自己炸了）。那不是「仍綠」，但也不是乾淨的斷言失敗，
    # 要單獨標出來去修走查，不能默默算過。
    caught[code] = (ok is False)
    mark = {True:"✗ 沒抓到（走查測不到這個部件）", False:"✓ 抓到",
            None:"⚠ 走查未跑完（自己炸了，去修走查）"}[ok]
    print(f"{code} 關掉「{desc}」→ {mark}（預期：{expect}）")
    if ok is not False:
        print("   ", "\n    ".join([l for l in out.split("\n") if "通過：" in l]))

restore()
again,_=run()
print("\n【還原後複驗】全部通過：",again)
print("突變全數被抓到：", all(v is True for v in caught.values()))
