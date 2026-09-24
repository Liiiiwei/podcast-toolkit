"""B1 專梯：前端「卡 key ↔ 時間版 cuts」邊界換算的實跑測試 + 與後端的差分等價。

為什麼要實跑 JS：本檔驗的是**演算法行為**，不是「原始碼裡有沒有那個字串」。
本機沒有 node，用 macOS 內建的 JavaScriptCore helper（jsc）跑 ES module。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from podcast_toolkit import assemble
from .conftest import editor_static_dir

JSC = Path(
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
)
pytestmark = pytest.mark.skipif(
    not JSC.is_file(), reason="本機沒有 JavaScriptCore helper（jsc），無法實跑 ES module"
)


def _run_js(body: str, tmp_path: Path):
    """在 timeline-core.js 的 module 脈絡下跑一段 JS，回傳它 print 出來的 JSON。"""
    core = editor_static_dir() / "timeline-core.js"
    local = tmp_path / "timeline-core.js"
    shutil.copyfile(core, local)  # 放同目錄，相對 import 才穩
    entry = tmp_path / "entry.mjs"
    entry.write_text(
        'import { cardKeysToCuts, cutsToCardSelection } from "./timeline-core.js";\n'
        + body,
        encoding="utf-8",
    )
    proc = subprocess.run(
        [str(JSC), "-m", str(entry)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"jsc 失敗：{proc.stderr or proc.stdout}"
    out = proc.stdout.strip().splitlines()
    assert out, f"jsc 沒有輸出：{proc.stderr}"
    return json.loads(out[-1])


ROWS_JS = (
    "const rows = ["
    '{key: 1, start: 0.0, end: 4.0},'
    '{key: 2, start: 4.0, end: 8.0},'   # 與 1 連續（flush）
    '{key: 3, start: 9.0, end: 12.0},'  # 與 2 之間有 1 秒真停頓
    '{key: "4:0", start: 12.0, end: 13.0},'
    '{key: "4:1", start: 13.0, end: 14.0},'
    "];\n"
)
# 同一份卡表的 Python 版（差分比對用）
ROWS_PY = [
    {"idx": 1, "start": 0.0, "end": 4.0, "text": "a"},
    {"idx": 2, "start": 4.0, "end": 8.0, "text": "b"},
    {"idx": 3, "start": 9.0, "end": 12.0, "text": "c"},
    {"idx": 4, "start": 12.0, "end": 14.0, "text": "d"},
]


def test_card_keys_to_cuts_is_one_interval_per_card(tmp_path):
    """逐卡一段、不預先合併——與後端舊 deletions 路徑（_from_idx）逐位元同形。"""
    got = _run_js(
        ROWS_JS + 'print(JSON.stringify(cardKeysToCuts([1, 2, 3], rows)));', tmp_path
    )
    assert got == [[0.0, 4.0], [4.0, 8.0], [9.0, 12.0]]


def test_sub_card_keys_supported(tmp_path):
    """切卡後的子卡 key（"4:1"）也換算得出時間，不需要先翻譯成 int idx。"""
    got = _run_js(ROWS_JS + 'print(JSON.stringify(cardKeysToCuts(["4:1"], rows)));', tmp_path)
    assert got == [[13.0, 14.0]]


def test_card_aligned_cut_maps_back_to_keys(tmp_path):
    got = _run_js(
        ROWS_JS
        + 'print(JSON.stringify(cutsToCardSelection([[0.0, 8.0], [9.0, 12.0]], rows)));',
        tmp_path,
    )
    assert got == {"keys": [1, 2, 3], "foreign": []}


def test_non_aligned_cut_stays_foreign_verbatim(tmp_path):
    """影片模式切出來的任意區間換不回卡 → 原樣留在 foreign，不擴寬成整張卡、不丟掉。"""
    got = _run_js(
        ROWS_JS + 'print(JSON.stringify(cutsToCardSelection([[1.5, 3.25]], rows)));',
        tmp_path,
    )
    assert got == {"keys": [], "foreign": [[1.5, 3.25]]}


def test_cut_spanning_a_real_pause_is_foreign_not_two_cards(tmp_path):
    """跨真停頓的一整段 cut：換成卡 key 會讓停頓復活 → 判 foreign，保真優先。"""
    got = _run_js(
        ROWS_JS + 'print(JSON.stringify(cutsToCardSelection([[4.0, 12.0]], rows)));',
        tmp_path,
    )
    assert got == {"keys": [], "foreign": [[4.0, 12.0]]}


def test_roundtrip_keys_to_cuts_to_keys(tmp_path):
    """卡 key → cuts → 卡 key：同一組 key 原封不動回來（邊界換算不吃掉東西）。"""
    got = _run_js(
        ROWS_JS
        + 'const cuts = cardKeysToCuts([1, 2, "4:0"], rows);\n'
        + 'print(JSON.stringify(cutsToCardSelection(cuts, rows).keys));',
        tmp_path,
    )
    assert got == [1, 2, "4:0"]


@pytest.mark.parametrize("cut_pad", [0.0, 0.15, 1.0])
def test_frontend_cuts_equal_legacy_deletions_path(tmp_path, cut_pad):
    """差分：前端換算出的 cuts 餵後端，結果必須與舊 deletions[idx] 路徑逐位元相同。

    這是「改存檔格式不改出片」的核心保證——cut_pad 三種設定都要成立。
    """
    deleted_idx = [1, 2, 3]
    cuts = _run_js(
        ROWS_JS
        + f'print(JSON.stringify(cardKeysToCuts({json.dumps(deleted_idx)}, rows)));',
        tmp_path,
    )
    via_cuts = assemble.cut_intervals_from_cfg(
        {"cuts": cuts, "cut_pad": cut_pad}, ROWS_PY
    )
    via_legacy = assemble.cut_intervals_from_cfg(
        {"deletions": deleted_idx, "cut_pad": cut_pad}, ROWS_PY
    )
    assert via_cuts == via_legacy
