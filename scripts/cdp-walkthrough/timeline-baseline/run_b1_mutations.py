#!/usr/bin/env python3
"""B1 突變測試：把每個受測行為逐一改回舊／錯行為，確認 verify_b1_cuts.py 會紅。

一支走查全綠只證明「沒拋例外」；只有突變會紅，才證明那些斷言真的在測東西。
每個突變只動一處、跑完整支走查、跑完立刻還原（原檔備份在 .mutbak）。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ST = ROOT / "podcast_toolkit" / "web" / "static"
CORE, APP, API = ST / "timeline-core.js", ST / "app.js", ST / "api.js"

MUTATIONS = [
    (
        "M1 cardKeysToCuts 不再把刪卡換成時間段（等於存檔不寫 cuts）",
        CORE,
        "    .filter((r) => set.has(r.key))",
        "    .filter((r) => false && set.has(r.key))",
        ["3b"],
    ),
    (
        "M2 cutsToCardSelection 把對不齊的 cut 擴寬成整張卡（不再產 foreign）",
        CORE,
        "    const covered = all.filter((r) => r.start >= s - tol && r.end <= e + tol);\n    let aligned = covered.length > 0;\n    if (aligned) {",
        "    const covered = all.filter((r) => r.end > s && r.start < e);\n    let aligned = covered.length > 0;\n    if (false) {",
        ["5b", "5c"],
    ),
    (
        "M3 app.js 載入端不讀 data.cuts（回到只認 deletions 的舊行為）",
        APP,
        "  if (Array.isArray(data.cuts) && data.cuts.length) {",
        "  if (false && Array.isArray(data.cuts) && data.cuts.length) {",
        ["4b"],
    ),
    (
        "M4 統計列不顯示換不回卡的剪段（靜默生效）",
        APP,
        "  if (foreign > 0) {",
        "  if (false) {",
        ["5b"],
    ),
    (
        "M5 cutToDiskTime 不減回偏移（存檔寫成顯示軸）",
        API,
        "  return [_ms(s + off), _ms(e + off)];",
        "  return [_ms(s), _ms(e)];",
        ["3b"],
    ),
    (
        "M6 cutFromDiskTime 不加回偏移（載入時對不回卡）",
        API,
        "  return [_ms(s - off), _ms(e - off)];",
        "  return [_ms(s), _ms(e)];",
        ["4b"],
    ),
]


def run():
    r = subprocess.run(
        [sys.executable, "-u", str(Path(__file__).with_name("verify_b1_cuts.py"))],
        capture_output=True,
        text=True,
    )
    tail = (r.stdout or "").strip().splitlines()
    failed = [l for l in tail if l.startswith("失敗：")]
    summary = [l for l in tail if "通過" in l]
    return (summary[-1] if summary else "?"), (failed[-1] if failed else "")


out = []
for name, path, old, new, expect in MUTATIONS:
    src = path.read_text(encoding="utf-8")
    if src.count(old) != 1:
        out.append({"突變": name, "狀態": "無法套用", "命中": src.count(old)})
        print(f"！{name}：目標字串命中 {src.count(old)} 次，跳過", flush=True)
        continue
    bak = path.with_suffix(path.suffix + ".mutbak")
    shutil.copy2(path, bak)
    try:
        path.write_text(src.replace(old, new), encoding="utf-8")
        summary, failed = run()
        red = [t for t in expect if t in failed]
        ok = bool(failed) and len(red) == len(expect)
        print(f"[{'RED 如預期' if ok else '未如預期'}] {name}\n    {summary}\n    {failed}", flush=True)
        out.append(
            {
                "突變": name,
                "預期變紅": expect,
                "實際": failed or summary,
                "判定": "RED（斷言有效）" if ok else "未如預期",
            }
        )
    finally:
        shutil.copy2(bak, path)
        bak.unlink()

# 還原後再跑一次，證明全綠不是因為檔案沒還原乾淨
summary, failed = run()
print(f"\n還原後回歸：{summary} {failed}", flush=True)
out.append({"突變": "（還原後回歸）", "實際": summary, "判定": "GREEN" if not failed else "還原失敗"})
Path(__file__).with_name("b1_mutations_result.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("寫入 b1_mutations_result.json", flush=True)
