"""軸位移併軌：前端 `alignShift` 與後端 `assemble.py` 的 `srt_total_shift` 必須是同一套算式。

背景（B3-2）：「顯示軸 ↔ 磁碟軸差多少」這件事一度有三份抄寫 —— app.js 載入端、
api.js 存檔端、video-edit-prototype.js。其中 api.js 存檔端**漏了 `audio.path` 守衛**：
episode.yaml 留著舊的 `audio.sync_offset` 但這集沒接外接音檔時，載入不位移、存檔卻減回去，
卡片時間每存一次就漂一個 sync_offset 秒，而且漂掉的量存進磁碟 SRT 後救不回來。
三份併成 `timeline-core.js: alignShift` 之後，用這支測試釘住它不再與後端漂移。

這支測試證明什麼、不證明什麼（誠實話）：
- 證明：JS 的 `alignShift` 對各種 cfg 組合算出來的值，等於後端兩行算式的值（含守衛）。
- 不證明：`cfg` → `state` 的欄位對照（app.js:2692-2693）沒寫錯 —— 那是端到端的事，
  由 CDP 走查 `verify_b3_guard_and_shift.py` 的 C 段實測（載入顯示值＋存檔回磁碟值）覆蓋。
- 後端那兩行若被改掉，`test_backend_formula_is_still_the_canon` 會紅（原始碼層的耦合警報），
  逼人回來重看 JS 這一份，而不是讓兩邊靜默漂移。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import editor_static_dir

JSC = Path(
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
)
pytestmark = pytest.mark.skipif(
    not JSC.is_file(), reason="本機沒有 JavaScriptCore helper（jsc），無法實跑 ES module"
)

REPO = Path(__file__).resolve().parents[1]


def _js_align_shift(states: list[dict], tmp_path: Path):
    """實跑 timeline-core.js 的 alignShift（不是掃原始碼字串），回傳每個 state 的位移。"""
    core = editor_static_dir() / "timeline-core.js"
    shutil.copyfile(core, tmp_path / "timeline-core.js")  # 同目錄，相對 import 才穩
    data = tmp_path / "states.json"
    data.write_text(json.dumps(states), encoding="utf-8")
    entry = tmp_path / "entry.mjs"
    entry.write_text(
        'import { alignShift } from "./timeline-core.js";\n'
        f"const states = JSON.parse(readFile({json.dumps(str(data))}));\n"
        "print(JSON.stringify(states.map((s) => alignShift(s))));",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [str(JSC), "-m", str(entry)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"jsc 失敗：{proc.stderr or proc.stdout}"
    out = proc.stdout.strip().splitlines()
    assert out, f"jsc 沒有輸出：{proc.stderr}"
    return json.loads(out[-1])


def _backend_shift(cfg: dict) -> float:
    """後端 assemble.py:1269-1282 的算式（逐行對照；守衛＝audio.path 有值才吃 sync_offset）。"""
    audio_cfg = cfg.get("audio") or {}
    audio_sync_offset = 0.0
    if audio_cfg.get("path"):
        audio_sync_offset = float(audio_cfg.get("sync_offset") or 0.0)
    return -audio_sync_offset + float(cfg.get("subtitle_offset_sec") or 0.0)


def _state_from_cfg(cfg: dict) -> dict:
    """前端 state 的三個欄位怎麼從 cfg 來（對照 app.js:2692-2693 與 api.js 的讀法）。"""
    audio = cfg.get("audio") or {}
    return {
        "audioPath": audio.get("path") or "",
        "audioSyncOffset": float(audio.get("sync_offset") or 0),
        "subtitleOffsetSec": float(cfg.get("subtitle_offset_sec") or 0),
    }


# 每筆都附「為什麼要有這個 case」，不是隨機湊數
CFGS = [
    ({}, "什麼都沒設 → 不位移"),
    ({"subtitle_offset_sec": 1.5}, "只有非破壞性字幕偏移"),
    ({"subtitle_offset_sec": -0.5}, "負偏移（字幕往前提）"),
    ({"audio": {"path": "a.wav", "sync_offset": 2.0}}, "真的接了外接音檔"),
    ({"audio": {"sync_offset": 2.0}}, "★守衛：yaml 留著 sync_offset 但沒接音檔 → 不位移"),
    (
        {"audio": {"path": "a.wav", "sync_offset": 2.0}, "subtitle_offset_sec": 0.123},
        "兩個位移疊加（毫秒級偏移，會咬到取整精度）",
    ),
    (
        {"audio": {"sync_offset": 2.0}, "subtitle_offset_sec": 0.123},
        "★守衛 + 毫秒偏移：B3-2 之前存檔端會在這裡漂 2 秒",
    ),
    ({"audio": {"path": "a.wav"}}, "有音檔但沒設 sync_offset"),
    ({"audio": {"path": "a.wav", "sync_offset": 0}}, "sync_offset=0（falsy）"),
    ({"audio": None, "subtitle_offset_sec": 0.25}, "audio 是 null（yaml 空節點）"),
]


def test_align_shift_matches_backend_formula(tmp_path):
    states = [_state_from_cfg(cfg) for cfg, _ in CFGS]
    got = _js_align_shift(states, tmp_path)
    assert len(got) == len(CFGS)
    bad = []
    for (cfg, why), g in zip(CFGS, got):
        exp = _backend_shift(cfg)
        # 用精確相等：兩邊是同順序的同一個 double 運算（-sync + subtitle），
        # 差一點就代表運算順序或欄位讀法不同，不該用容忍值蓋過去。
        if g != exp:
            bad.append({"cfg": cfg, "為什麼要測": why, "JS": g, "後端": exp})
    assert not bad, f"前後端位移不一致：{json.dumps(bad, ensure_ascii=False, indent=2)}"


def test_align_shift_tolerates_missing_state(tmp_path):
    """alignShift 對 null / 空物件要回 0，不能拋 —— 它在 render 迴圈裡被呼叫。"""
    got = _js_align_shift([{}], tmp_path)
    assert got == [0]


def test_backend_formula_is_still_the_canon():
    """原始碼層耦合警報：後端那兩行（含守衛的縮排）若被改動，這裡先紅。

    這不是「掃字串當測試」，而是刻意的變更偵測 —— 它唯一的職責是：後端算式一改，
    就有人被強迫回來看 timeline-core.js 的 alignShift 要不要跟著改。
    """
    src = (REPO / "podcast_toolkit" / "assemble.py").read_text(encoding="utf-8")
    assert (
        "srt_total_shift = -audio_sync_offset + subtitle_offset" in src
    ), "後端位移算式已改（-sync + subtitle）→ 回頭同步 timeline-core.js: alignShift"
    assert (
        'subtitle_offset = float(cfg.get("subtitle_offset_sec") or 0.0)' in src
    ), "後端 subtitle_offset 讀法已改 → 回頭同步 alignShift"
    # 守衛：`audio_sync_offset = float(...)` 必須縮在 `if audio_cfg.get("path"):` 內（8 空格）
    m = re.search(
        r'\n    if audio_cfg\.get\("path"\):\n(?:.*\n)*?'
        r'        audio_sync_offset = float\(audio_cfg\.get\("sync_offset"\) or 0\.0\)\n',
        src,
    )
    assert m, (
        "後端的 audio.path 守衛結構已變（sync_offset 不再只在有音檔時生效）→ "
        "回頭同步 alignShift 的 audioPath 守衛"
    )
