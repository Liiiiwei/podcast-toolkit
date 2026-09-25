#!/usr/bin/env python3
"""B3 走查的突變測試：逐個把 B3 的改動退回舊行為，確認 verify_b3_guard_and_shift.py
會紅在預期的項目上（只印值不斷言等於未測；只有突變能證明斷言真的在守東西）。

跑法：/usr/bin/python3 -u run_b3_mutations.py（headless Chrome CDP :9331 要自己先開）
產物：五個突變各自的紅項清單 + 還原回歸，逐項對照見 MUTATIONS.md #19～#23。
"""
import subprocess, sys, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
APP = REPO / "podcast_toolkit/web/static/app.js"
API = REPO / "podcast_toolkit/web/static/api.js"
CORE = REPO / "podcast_toolkit/web/static/timeline-core.js"
DRIVE = HERE / "verify_b3_guard_and_shift.py"
BAK = Path("/private/tmp/b3-mutate-bak")
BAK.mkdir(exist_ok=True)

CARD_LOOP = """  if (!inForeignCut) {
    for (const r of expandedCards()) {
      if (!state.deletions.has(r.key) && t >= r.start && t < r.end) return t;
    }
  }
"""

MUTS = {
    "M1 退回卡優先守門（B3-1 之前）": (
        APP,
        CARD_LOOP,
        """  void inForeignCut; // 突變：卡優先守門
  for (const r of expandedCards()) {
    if (!state.deletions.has(r.key) && t >= r.start && t < r.end) return t;
  }
""",
    ),
    "M2 整個守門拿掉": (APP, CARD_LOOP, "  void inForeignCut; // 突變：無守門\n"),
    "M3 存檔端另抄一份無 audioPath 守衛的位移": (
        API,
        "  return -alignShift(state);\n",
        "  return -((state.audioSyncOffset ? -state.audioSyncOffset : 0)"
        " + (state.subtitleOffsetSec || 0)); // 突變\n",
    ),
    "M4 毫秒取整退回 2 位小數": (
        API,
        "const _ms = (v) => Math.round(v * 1000) / 1000;",
        "const _ms = (v) => Math.round(v * 100) / 100; // 突變",
    ),
    "M5 併軌後的 alignShift 一起掉守衛（載入＋存檔同時）": (
        CORE,
        "  const audioShift = s.audioPath && s.audioSyncOffset ? -s.audioSyncOffset : 0;",
        "  const audioShift = s.audioSyncOffset ? -s.audioSyncOffset : 0; // 突變",
    ),
}

for f in (APP, API, CORE):
    shutil.copy2(f, BAK / f.name)

summary = []
try:
    for name, (f, old, new) in MUTS.items():
        txt = f.read_text(encoding="utf-8")
        assert txt.count(old) == 1, f"{name}: 待換字串命中 {txt.count(old)} 次"
        f.write_text(txt.replace(old, new), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, "-u", str(DRIVE)], capture_output=True, text=True, cwd=str(DRIVE.parent)
        )
        fails = [
            l.split("  got=")[0].replace("[FAIL] ", "")
            for l in r.stdout.splitlines()
            if l.startswith("[FAIL]")
        ]
        tot = [l for l in r.stdout.splitlines() if "通過" in l]
        print(f"\n### {name}\nrc={r.returncode} {tot[-1] if tot else '（無總結）'}")
        for x in fails:
            print("  紅：" + x)
        if not fails:
            print("  ！沒有任何紅 → 這支走查測不到這個突變")
        summary.append((name, fails))
        shutil.copy2(BAK / f.name, f)
finally:
    for f in (APP, API, CORE):
        shutil.copy2(BAK / f.name, f)
    print("\n（已還原三支 JS）")

print("\n===== 彙總 =====")
for name, fails in summary:
    print(f"{name} → {len(fails)} 紅: {'; '.join(x[:14] for x in fails) or '無（壞）'}")
