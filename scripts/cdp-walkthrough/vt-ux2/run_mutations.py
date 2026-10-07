#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第二～五梯 UX 走查的突變測試：證明 drive.py 的斷言真的在測東西。

做法：把 static 複製到 /private/tmp 的沙盒（不動版控內的檔），對副本做一處字串替換
（還原成舊行為／關掉一個部件），跑 drive.py，確認「該突變指定的斷言」轉紅；
再把副本還原、重跑，確認轉綠。部件逐一關，不做「全開 vs 全關」。

用法：python3 -u run_mutations.py            # 全部
      python3 -u run_mutations.py MN9 MN12   # 只跑名稱含這些字的
結束碼：每個突變都「紅→綠」成立才是 0。

「檔」那一欄是 DRIVE 的突變不改產品碼，而是改走查自己的行為（原字串欄放環境變數名、
突變後欄放值），用來證明「檢查走查環境」的斷言（N17）不是恆綠。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
STATIC = os.path.join(REPO, "podcast_toolkit", "web", "static")
JS, HTML = "video-edit-prototype.js", "video-edit-prototype.html"
ICONS = "icons.js"
DRIVE = "（走查自身）"

# (名稱, 檔, 原字串, 突變後, 必須轉紅的斷言 id)
MUTATIONS = [
    ("M9-順序（改回照到達順序列）", JS,
     "Object.keys(LOAD_SOURCES).filter(loadFailed)",
     "Object.keys(loadState).filter(loadFailed)",
     ["9.sa.keys", "9.alld.keys"]),
    ("M9-術語（改回把技術細節當主文）", JS,
     "li.textContent = `${LOAD_SOURCES[k].label}：${loadFailText(k)}`;",
     "li.textContent = `${LOAD_SOURCES[k].label}：${loadState[k].detail}`;",
     ["9.v.term", "9.w.term", "9.s.term", "9.a.term"]),
    ("M9-分行（清單項目改回接成同一行）", HTML,
     "      .vt-empty-list li {\n        margin: 2px 0;",
     "      .vt-empty-list li {\n        display: inline;\n        margin: 2px 0;",
     ["9.vw.line", "9.all.line"]),
    ("M9-存檔後靜默（存對齊後重載字幕失敗不回報）", JS,
     'setLoadFail("subs", subs ? null : SUBS_FAIL_DETAIL)',
     'void (subs ? null : SUBS_FAIL_DETAIL)',
     ["9.save.fail"]),
    ("M6-抓柄（拿掉常駐可見的抓柄）", HTML,
     '      .vt-playhead-grip::after {\n        content: "";',
     '      .vt-playhead-grip::after {\n        content: none;',
     ["6.vis"]),
    ("M12-popover（ⓘ 不綁開關）", JS,
     'bindPopover("vt-keys-toggle", "vt-keys-pop");',
     '',
     ["12.h900.open", "12.h760.open", "12.h900.kbd", "R.al.excl"]),
    ("M14-文案（標頭註記改回重複的那句）", HTML,
     "在字幕列點「卡」也能新增",
     "拖版型到藍軌，或在字幕列點「卡」",
     ["14.once", "14.note"]),
    ("MN1-按鈕層（未載入也不鎖欄位與儲存鈕）", JS,
     "const locked = DEMO || !loaded;",
     "const locked = DEMO;",
     ["N1.f.lock"]),
    # 載入中 init 還沒跑，JS 的鎖管不到；這一段的鎖來自 markup 的預設 disabled
    ("MN1-載入中（儲存鈕 markup 不預設 disabled）", HTML,
     '              id="vt-al-save"\n              disabled\n',
     '              id="vt-al-save"\n',
     ["N1.l.lock"]),
    ("MN1-資料層（拿掉 saveAlignment 的未載入防線）", JS,
     "    if (!alignLoaded()) {\n      if (status) {",
     "    if (false) {\n      if (status) {",
     ["N1.f.data", "N1.f.click", "N1.f.keep"]),
    ("MN1-留空（未載入時欄位照樣顯示 state 的 0）", JS,
     "const blank = !DEMO && !loaded;",
     "const blank = false;",
     ["N1.f.blank"]),
    ("M13-圖示來源（不載入 icons.js）", HTML,
     '    <script src="icons.js"></script>\n',
     '',
     ["13.one", "13.size", "13.same"]),
    ("M13-命中（圖示吃掉點擊）", HTML,
     "        flex-shrink: 0;\n        pointer-events: none;",
     "        flex-shrink: 0;\n        pointer-events: auto;",
     ["12.h900.btn", "12.h760.btn", "13.hit"]),
    ("M13-命中（逐句工具鈕的 svg 吃掉點擊）", HTML,
     "        height: 13px;\n        pointer-events: none;",
     "        height: 13px;\n        pointer-events: auto;",
     ["13.hit"]),
    # ── 第四梯 ──
    ("MN2-區塊（載入中不標 aria-busy）", JS,
     'el.setAttribute("aria-busy", "true")',
     'void 0',
     ["N2.busy"]),
    ("MN2-字樣（載入中不顯示字樣）", JS,
     "note.hidden = busy.length === 0;",
     "note.hidden = true;",
     ["N2.text"]),
    ("MN2-收起（來源有結果後不記錄 → 載入中永遠不收）", JS,
     "everSettled.add(key);",
     "",
     ["N2.done", "N2.fail"]),
    ("MN2-波形成功（波形載入成功不回報）", JS,
     'setLoadFail("wave", null);',
     "",
     ["N2.done"]),
    ("MN5-讓位（刪除鈕貼右緣、蓋住右把手）", HTML,
     "        right: 12px;\n        top: 50%;",
     "        right: 0;\n        top: 50%;",
     ["N5.handle", "N5.narrow"]),
    ("MN5-按下（刪除鈕的 pointerdown 冒泡到卡本體）", JS,
     'b.addEventListener("pointerdown", (e) => e.stopPropagation());',
     "",
     ["N5.click", "N5.narrow"]),
    ("MN5-門檻（放不下也硬畫刪除鈕）", JS,
     "if (!c || (narrow && c.id !== state.selectedCard)) return;",
     "if (!c) return;",
     ["N5.tiny", "N13.click"]),
    ("MN6-工具鈕（「併」改回借 chevron-up）", JS,
     'merge: "merge", // 往上併入前一句',
     'merge: "chevron-up",',
     ["N6.icon"]),
    ("MN6-圖示庫（icons.js 沒有 merge）", ICONS,
     "    merge:\n",
     "    merge_off:\n",
     ["N6.icon"]),
    ("MN7-標籤（剪除段標籤改回 ✕ 字符）", JS,
     'label.append(iconSpan("x", 11, "✕"), t);',
     'label.append("✕ ", t);',
     ["N7.label"]),
    ("MN8-對齊（時間軸標頭改回 baseline）", HTML,
     "文字高 2.5px */\n        align-items: center;",
     "文字高 2.5px */\n        align-items: baseline;",
     ["N8.mid"]),
    ("MN9-訊息（重讀失敗照樣說已儲存）", JS,
     "const reloaded = alignLoaded();",
     "const reloaded = true;",
     ["N9.msg"]),
    ("MN11-顯示（不讀外接音檔的偏移）", JS,
     "state.audioSyncOffset = audio ? numOr(audio.sync_offset, 0) : 0;",
     "state.audioSyncOffset = 0;",
     ["N11.show", "N11.rt"]),
    ("MN11-寫入（有外接音檔也不送 audio）", JS,
     "if (state.audioPath) {",
     "if (false) {",
     ["N11.save", "N11.rt"]),
    ("MN12-空白鍵（焦點在按鈕上也照樣播放／暫停）", JS,
     "if (isSpaceActivated(e.target)) return;",
     "",
     ["N12.btn"]),
    # ── 第五梯 ──
    ("MN13-外側（窄卡的鈕照舊塞在卡裡）", JS,
     "      if (narrow) {\n",
     "      if (false) {\n",
     ["N13.show", "N13.hit", "N13.handle", "N13.click", "N13.flip"]),
    ("MN13-選取才畫（選取了窄卡也不畫鈕）", JS,
     "if (!c || (narrow && c.id !== state.selectedCard)) return;",
     "if (!c || narrow) return;",
     ["N13.show", "N13.hit", "N13.handle", "N13.click", "N13.flip"]),
    # 第五梯第二輪：放置判斷改成實際幾何（軌道邊界＋不疊別張卡），原本那行已不存在 →
    # 改成拿掉「不得超出軌道右緣」這一項，突變的意思不變
    ("MN13-翻面（貼尾端也一律放右邊 → 被軌道裁掉）", JS,
     "          r <= tr.right &&\n",
     "",
     ["N13.flip"]),
    ("MN13-避鄰卡（外側鈕不管會不會疊到別張卡）", JS,
     "          !others.some((o) => l < o.right && r > o.left);",
     "          true;",
     ["N13.adj0", "N13.adj6", "N13.adjflip", "N13.adjnone"]),
    ("MN13-貼尾端（右邊出軌時翻左不看左鄰卡）", JS,
     "} else if (fits(me.left - need, me.left)) {",
     "} else if (me.right + need > tr.right || fits(me.left - need, me.left)) {",
     ["N13.adjend"]),
    ("MN13-間距（外側鈕與鄰卡之間不留空隙）", JS,
     "const need = CARD_DEL_OUT_W + CARD_DEL_OUT_GAP;",
     "const need = CARD_DEL_OUT_W;",
     ["N13.gap"]),
    ("MN13-把手不見（窄卡不畫把手 → 走查要記紅並跑完，不能崩潰）", JS,
     '        b.classList.add("is-out");\n',
     '        b.classList.add("is-out");\n        el.querySelectorAll(".vt-card-h").forEach((n) => n.remove());\n',
     ["N13.hit", "N13.handle", "N13.adjnone"]),
    ("MN13-別張卡（選了別張卡，窄卡也出外側鈕）", JS,
     "if (!c || (narrow && c.id !== state.selectedCard)) return;",
     "if (!c || (narrow && state.selectedCard == null)) return;",
     ["N13.other"]),
    # 鈕疊回卡的右把手上：拖把手變成點到鈕、卡被刪 → 後面取不到把手；走查要照樣跑完並記紅
    ("MN13-貼卡（外側鈕往回疊在卡的右把手上）", JS,
     "`calc(${el.style.left} + ${el.style.width} + 2px)`",
     "`calc(${el.style.left} + ${el.style.width} - 10px)`",
     ["N13.hit", "N13.handle"]),
    ("MN13-清掉（重畫時不清舊的外側鈕）", JS,
     'track.querySelectorAll(".vt-card-del.is-out").forEach((n) => n.remove());',
     "",
     ["N13.handle", "N13.click"]),
    ("MN14-訊息（字幕重載失敗照樣只說已儲存）", JS,
     "if (reloaded && !subs) {",
     "if (false) {",
     ["N14.msg"]),
    ("MN14-重讀（重讀這一集失敗時也只點名字幕）", JS,
     "if (reloaded && !subs) {",
     "if (!subs) {",
     ["N14.both"]),
    ("MN17-前景（導頁前不把分頁叫回前景）", DRIVE,
     "VT_DRIVE_NO_FRONT", "1",
     ["N17.vis"]),
    ("MN18-豁免縮小（空白鍵豁免只認 #vt-keys-toggle）", JS,
     """t.closest("button, summary, a[href], [role='button']")""",
     't.closest("#vt-keys-toggle")',
     ["N18.play", "N18.zoom", "N18.summary"]),
    ("MN19-字幕清單（字幕不列入載入中區塊）", JS,
     '    subs: "#vt-line-list",\n',
     "",
     ["N19.busy"]),
    ("MN19-解除（來源有結果後不記錄 → aria-busy 不解除）", JS,
     "everSettled.add(key);",
     "",
     ["N19.done"]),
    ("MN20-select（下拉選單不算輸入目標）", JS,
     '      tag === "SELECT" ||\n',
     "",
     ["N20.select"]),
    ("MN20-input（輸入欄不算輸入目標）", JS,
     '      tag === "INPUT" ||\n',
     "",
     # N18.text 不列：字幕文字框自己的 keydown 先 stopPropagation，這一層拿掉它仍然綠
     # （兩層防線，單點突變打不紅；已記入計畫附錄 N21）
     ["N20.num", "N20.color"]),
]


def run_drive(base, out, env=None):
    if os.path.exists(out):
        os.remove(out)
    subprocess.run([sys.executable, "-u", os.path.join(HERE, "drive.py"),
                    "--base", base, "--json", out],
                   env=dict(os.environ, **(env or {})),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.exists(out):
        return None  # 走查沒跑完（環境中止）≠ 斷言轉紅，不能當成「紅」
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def main():
    box = tempfile.mkdtemp(prefix="vt-ux2-sbx-", dir="/private/tmp")
    sbx = os.path.join(box, "static")
    shutil.copytree(STATIC, sbx)
    srv = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "serve.py"), "--static", sbx],
                           stdout=subprocess.PIPE)
    base = "http://127.0.0.1:%s" % srv.stdout.readline().decode().strip().split("=")[1]
    out = os.path.join(box, "result.json")
    all_ok = True
    try:
        base_res = run_drive(base, out)
        green0 = bool(base_res) and all(base_res.values())
        print("基準（未突變的副本）：%s（%s 條）" % ("全綠" if green0 else "有紅／沒跑完",
                                            len(base_res or {})))
        if not green0:
            print("✗ 基準就不是全綠，突變結果沒有意義，中止")
            return 2
        only = [a for a in sys.argv[1:] if not a.startswith("-")]
        for name, fn, old, new, targets in MUTATIONS:
            if only and not any(o in name for o in only):
                continue
            if fn == DRIVE:
                red = run_drive(base, out, {old: new})
            else:
                fp = os.path.join(sbx, fn)
                src = io.open(fp, encoding="utf-8").read()
                if src.count(old) != 1:
                    print("✗ %s：原字串出現 %d 次（要剛好 1 次），突變沒套上" % (name, src.count(old)))
                    all_ok = False
                    continue
                io.open(fp, "w", encoding="utf-8").write(src.replace(old, new))
                red = run_drive(base, out)
                io.open(fp, "w", encoding="utf-8").write(src)  # 還原
            green = run_drive(base, out)
            if red is None or green is None:
                print("✗ %s：走查沒跑完（紅=%s 綠=%s）" % (name, red is not None, green is not None))
                all_ok = False
                continue
            went_red = [t for t in targets if red.get(t) is False]
            others = sorted(k for k, v in red.items() if not v and k not in targets)
            back_green = all(green.values()) and len(green) == len(base_res)
            ok = (went_red == targets) and back_green
            all_ok = all_ok and ok
            print("%s %s｜指定轉紅 %d／%d%s｜連帶轉紅 %d 條%s｜還原後全綠=%s" % (
                "✓" if ok else "✗", name, len(went_red), len(targets),
                "" if went_red == targets else "（沒紅：%s）" % [t for t in targets if t not in went_red],
                len(others), ("：" + ", ".join(others[:8])) if others else "", back_green))
            sys.stdout.flush()
    finally:
        srv.terminate()
        subprocess.run(["trash", box], check=False)
    print("\n===== 突變測試：%s =====" % ("全部紅→綠成立" if all_ok else "有不成立的"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
