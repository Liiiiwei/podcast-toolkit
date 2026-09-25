#!/usr/bin/env python3
"""B3-3 突變測試：證明 tests/test_new_card_keys_never_deleted.py 真的在測東西。

與同目錄其他 run_*_mutations.py 的差別：這支驅動的是 pytest（原始碼層不變式），
不是 CDP 走查。放在一起是為了讓 B1/B2/B3 的突變紀錄同一個地方查得到。

每個突變＝「把新增卡也丟進 deletions 的路徑打開」的一種寫法，預期單點變紅。
跑法：python3 -u scripts/cdp-walkthrough/timeline-baseline/run_b3_3_mutations.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
STATIC = REPO / "podcast_toolkit" / "web" / "static"
BAK = Path("/private/tmp/b3-3-mutate-bak")
TARGETS = ["app.js", "timeline.js", "api.js"]
TEST = "tests/test_new_card_keys_never_deleted.py"

# (代號, 說明, 檔名, 原字串, 換成)
MUTATIONS = [
    (
        "N1",
        "框選不再排除 new: 鍵（新增卡會被框進 deletions）",
        "timeline.js",
        '            !String(r.key).startsWith("new:") && r.start < hi && r.end > lo,\n',
        "            r.start < hi && r.end > lo, // 突變\n",
    ),
    (
        "N2",
        "renderCards 的新增卡分支不再 continue（往下走到刪除鈕）",
        "app.js",
        "      renderNewCardRow(r, frag);\n      continue;\n",
        "      renderNewCardRow(r, frag); // 突變：拿掉 continue\n",
    ),
    (
        "N3",
        "載入順序顛倒：先由 cuts 反推選取才清 newCards",
        "app.js",
        "  state.newCards = [];\n",
        "  // 突變：清空搬到反推選取之後\n",
    ),
    (
        "N4",
        "多一個沒審過的 deletions 寫入點",
        "app.js",
        "        state.deletions.add(key);\n",
        "        state.deletions.add(key);\n        if (0) state.deletions.add(r.key); // 突變\n",
    ),
    (
        "N5",
        "拿掉存檔端最後一道防禦性 filter",
        "api.js",
        '        [...state.deletions].filter((k) => !String(k).startsWith("new:")),\n',
        "        [...state.deletions], // 突變\n",
    ),
]


def backup() -> None:
    BAK.mkdir(parents=True, exist_ok=True)
    for name in TARGETS:
        shutil.copyfile(STATIC / name, BAK / name)


def restore() -> None:
    for name in TARGETS:
        if (BAK / name).is_file():
            shutil.copyfile(BAK / name, STATIC / name)


def apply_mutation(name: str, old: str, new: str) -> None:
    p = STATIC / name
    src = p.read_text(encoding="utf-8")
    n = src.count(old)
    assert n == 1, f"突變目標在 {name} 出現 {n} 次（需恰好 1 次）：{old!r}"
    p.write_text(src.replace(old, new), encoding="utf-8")


def run_tests() -> tuple[int, list[str], str]:
    proc = subprocess.run(
        ["/usr/bin/python3", "-m", "pytest", "-q", TEST, "--tb=no", "-rf"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
    )
    out = proc.stdout + proc.stderr
    failed = sorted(
        {
            ln.split("::", 1)[1].split()[0]
            for ln in out.splitlines()
            if ln.startswith("FAILED ") and "::" in ln
        }
    )
    summary = next(
        (ln for ln in out.splitlines() if " passed" in ln or " failed" in ln), "(無摘要)"
    )
    return len(failed), failed, summary


def main() -> int:
    backup()
    results = []
    try:
        n_fail, failed, summary = run_tests()
        print(f"[基準] 未突變：{summary.strip()}")
        assert n_fail == 0, "未突變狀態就有紅的，先修好再跑突變"
        for code, desc, name, old, new in MUTATIONS:
            restore()
            apply_mutation(name, old, new)
            n_fail, failed, summary = run_tests()
            print(f"[{code}] {desc} → {n_fail} 紅：{failed}")
            results.append(
                {"代號": code, "突變": desc, "檔": name, "紅的測試": failed}
            )
    finally:
        restore()
        print("（已還原 " + "、".join(TARGETS) + "）")
    out = Path(__file__).with_name("b3_3_mutations_result.json")
    out.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"結果寫入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
