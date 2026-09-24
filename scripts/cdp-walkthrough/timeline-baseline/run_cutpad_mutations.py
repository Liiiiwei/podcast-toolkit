#!/usr/bin/env python3
"""附錄 1 突變測試：把每個受測行為逐一改回舊／錯行為，確認 verify_cutpad_preview.py 會紅。

一支走查全綠只證明「沒拋例外」；只有突變會紅，才證明那些斷言真的在測東西。
每個突變只動一處、跑完整支走查、跑完立刻還原（原檔備份在 .mutbak）。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "podcast_toolkit" / "web"
ST = WEB / "static"
CORE, APP, IO = ST / "timeline-core.js", ST / "app.js", WEB / "episode_io.py"

MUTATIONS = [
    (
        "M1 前端把 cut_pad 讀成 0（等於併軌前的無 pad 行為）",
        APP,
        "  state.cutPad = Number(data.cut_pad) || 0;",
        "  state.cutPad = 0;",
        ["A4b", "A4c", "C3a"],
    ),
    (
        "M2 預覽跳段不含 foreignCuts（影片模式剪的段預覽不跳）",
        APP,
        "  const raw = [...state.foreignCuts];",
        "  const raw = [];",
        ["C3a"],
    ),
    (
        "M3 padAndMergeCuts 左側不延伸",
        CORE,
        "    const ns = Math.max(s - pad, leftLimit, 0);",
        "    const ns = Math.max(s, leftLimit, 0);",
        ["A4b"],
    ),
    (
        "M4 padAndMergeCuts 右側不延伸",
        CORE,
        "    const ne = Math.min(e + pad, rightLimit);",
        "    const ne = Math.min(e, rightLimit);",
        # 右側不延伸 → 三個跳段落點全部往前縮，A4b 量的也是同一個落點，故一併紅
        ["A4b", "A4c", "C3a"],
    ),
    (
        "M5 右界不夾在保留卡起點（pad 直接咬進下一張卡的語音）",
        CORE,
        "    const ne = Math.min(e + pad, rightLimit);",
        "    const ne = Math.max(e + pad, rightLimit);",
        ["A4b", "A4c", "C3a"],
    ),
    (
        "M6 後端不下放 cut_pad（前端永遠拿不到值）",
        IO,
        '        "cut_pad": float(ep.cfg.get("cut_pad") or 0),\n',
        "",
        ["A1"],
    ),
]


def run():
    r = subprocess.run(
        [sys.executable, "-u", str(Path(__file__).with_name("verify_cutpad_preview.py"))],
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
Path(__file__).with_name("cutpad_mutations_result.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("寫入 cutpad_mutations_result.json", flush=True)
