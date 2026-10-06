#!/usr/bin/env python3
"""合併 podcast_result.json + video_result.json 成單一 baseline 檔（T0 基準線）。

用途：docs/plans/2026-09-23-unified-timeline-core.md 動刀前的鐵證——之後每一步
重構都拿這份 baseline 重跑 drive_podcast.py / drive_video_proto.py 比對防回歸。

跑法：/usr/bin/python3 -u assemble_baseline.py（前提：兩支 driver 都已跑過、
本目錄有 podcast_result.json 與 video_result.json）
輸出：baseline-2026-09-23.json（本目錄）
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "baseline-2026-09-23.json"


def load(name):
    p = HERE / name
    if not p.exists():
        raise SystemExit(f"缺 {p}，先跑對應的 drive_*.py")
    return json.loads(p.read_text(encoding="utf-8"))


def main():
    podcast = load("podcast_result.json")
    video = load("video_result.json")

    def tally(mode):
        # status 感知（新 schema）：skip 不計入分母；舊 schema（無 status）退回看 ok。
        results = mode["results"]
        n_pass = sum(1 for r in results if r.get("status", "pass" if r["ok"] else "fail") == "pass")
        n_fail = sum(1 for r in results if r.get("status", "pass" if r["ok"] else "fail") == "fail")
        n_skip = sum(1 for r in results if r.get("status") == "skip")
        return {"total": n_pass + n_fail, "pass": n_pass, "fail": n_fail, "skip": n_skip}

    baseline = {
        "captured_at": "2026-09-23",
        "purpose": "docs/plans/2026-09-23-unified-timeline-core.md 動刀前 T0 基準線，之後每步重構重跑兩支 driver 比對防回歸",
        "note": (
            "2026-09-23 發現有並行 session 已在對 timeline.js/video-edit-prototype.*"
            " 動刀（working tree 出現未追蹤修改＋新檔 timeline-core.js）。為確保這份"
            " baseline 量的是「動刀前」的 pristine 狀態，serve_podcast.py 與"
            " range_server.py 已改成服務 `git archive HEAD` 產生的快照"
            "（/private/tmp/pt-timeline-baseline/head-snapshot/），不服務 live"
            " working tree。本檔數字＝對 git HEAD 版本量測的結果。"
        ),
        "modes": {
            "podcast": {
                "driver": "drive_podcast.py",
                "target": "http://127.0.0.1:8795/ 的 #card-timeline（timeline.js + app.js）",
                "summary": tally(podcast),
                "results": podcast["results"],
            },
            "video-edit-prototype": {
                "driver": "drive_video_proto.py",
                "target": "http://127.0.0.1:8796/video-edit-prototype.html?demo=1 的 #vt-tracks",
                "summary": tally(video),
                "results": video["results"],
            },
        },
    }
    OUT.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    p = baseline["modes"]["podcast"]["summary"]
    v = baseline["modes"]["video-edit-prototype"]["summary"]
    print(f"寫入 {OUT}")
    print(f"podcast: {p['pass']}/{p['total']} PASS")
    print(f"video-edit-prototype: {v['pass']}/{v['total']} PASS")


if __name__ == "__main__":
    main()
