#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第二梯 UX 走查的突變測試：證明 drive.py 的斷言真的在測東西。

做法：把 static 複製到 /private/tmp 的沙盒（不動版控內的檔），對副本做一處字串替換
（還原成舊行為／關掉一個部件），跑 drive.py，確認「該突變指定的斷言」轉紅；
再把副本還原、重跑，確認轉綠。部件逐一關，不做「全開 vs 全關」。

用法：python3 -u run_mutations.py
結束碼：每個突變都「紅→綠」成立才是 0。
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
]


def run_drive(base, out):
    if os.path.exists(out):
        os.remove(out)
    subprocess.run([sys.executable, "-u", os.path.join(HERE, "drive.py"),
                    "--base", base, "--json", out],
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
