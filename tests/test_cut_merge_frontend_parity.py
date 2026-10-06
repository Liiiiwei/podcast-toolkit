"""刪段合併規則併軌：前端 padAndMergeCuts 與後端 _pad_and_merge_cuts 必須是同一套規則。

為什麼要實跑 JS：驗的是**演算法行為**而不是「原始碼裡有沒有那個字串」。本機沒有 node，
用 macOS 內建的 JavaScriptCore helper（jsc）跑 ES module。

背景：前端原本自帶另一套合併（無 pad、門檻 0.05、只夾下一張保留卡起點），預覽跳段每段
都比成品短 cut_pad 秒／側。移植成共用純函式後，用這支差分測試釘住兩邊不再各自漂移。

2026-09-25 補：加入「相鄰卡時間戳重疊」的 case（OVERLAP_CARDS / SANDWICH_CARDS）。
那是走查 B5 量到的 0.75 秒落差來源 —— 刪卡產生的區間本身要夾在保留卡語音之外，
而影片模式自己框的區間（foreign cut）不夾。兩邊各自實作，靠這支測試釘住逐位元相同。
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from podcast_toolkit.assemble import _pad_and_merge_cuts
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
    shutil.copyfile(core, tmp_path / "timeline-core.js")  # 同目錄，相對 import 才穩
    entry = tmp_path / "entry.mjs"
    entry.write_text(
        'import { padAndMergeCuts } from "./timeline-core.js";\n' + body,
        encoding="utf-8",
    )
    proc = subprocess.run(
        [str(JSC), "-m", str(entry)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"jsc 失敗：{proc.stderr or proc.stdout}"
    out = proc.stdout.strip().splitlines()
    assert out, f"jsc 沒有輸出：{proc.stderr}"
    return json.loads(out[-1])


def _js_batch(cases: list[dict], tmp_path: Path):
    """一次 jsc 跑完整批 case（啟動成本高，不要每個 case 開一次）。"""
    data = tmp_path / "cases.json"
    data.write_text(json.dumps(cases), encoding="utf-8")
    return _run_js(
        f'const cases = JSON.parse(readFile({json.dumps(str(data))}));\n'
        "const out = cases.map((c) => padAndMergeCuts(c.intervals, c.cards, c.pad));\n"
        "print(JSON.stringify(out));",
        tmp_path,
    )


def _py(case: dict):
    got = _pad_and_merge_cuts(
        [(float(a), float(b)) for a, b in case["intervals"]], case["cards"], case["pad"]
    )
    return [[s, e] for s, e in got]


CARDS = [
    {"start": 0.0, "end": 4.0},
    {"start": 4.0, "end": 8.0},    # 與上一張連續（flush）
    {"start": 9.0, "end": 12.0},   # 中間 1 秒真停頓
    {"start": 12.5, "end": 14.0},
    {"start": 20.0, "end": 24.0},  # 前方 6 秒大停頓
]


# ★ 相鄰卡時間戳重疊（Whisper 逐字時間戳的常態）：#2 被刪、#3 的頭往回咬進它 0.75 秒
OVERLAP_CARDS = [
    {"start": 4.25, "end": 6.65},
    {"start": 7.35, "end": 8.25},    # 這張被刪
    {"start": 7.50, "end": 10.15},   # 頭與上一張重疊 0.75s（＝走查 B 階段的情境）
    {"start": 11.05, "end": 13.05},
]

# 兩張保留卡把被刪卡的整段語音都蓋住 → 夾完歸零 → 這段不剪（後端另外印警告）
SANDWICH_CARDS = [
    {"start": 1.0, "end": 2.3},   # 尾巴落在刪段內
    {"start": 2.0, "end": 2.5},   # 這張被刪
    {"start": 2.2, "end": 3.0},   # 頭落在刪段內
]


def _case(intervals, pad, cards=None):
    return {"intervals": intervals, "cards": cards if cards is not None else CARDS, "pad": pad}


FIXED_CASES = [
    # pad=0：原樣返回、不合併（後端的向後相容分支）
    _case([[4.0, 8.0], [9.0, 12.0]], 0.0),
    # 單段往兩側各吃 pad，但夾在鄰卡語音邊界內（左鄰 end=8.0、右鄰 start=12.5）
    _case([[9.0, 12.0]], 0.15),
    # pad 給很大：吃滿到鄰卡邊界就停（不咬進保留語音）
    _case([[9.0, 12.0]], 5.0),
    # 連刪兩張、中間無保留卡 → 併成整段（跨停頓也併）
    _case([[4.0, 8.0], [9.0, 12.0]], 0.15),
    # 中間夾著保留卡 → 不併
    _case([[0.0, 4.0], [9.0, 12.0]], 0.15),
    # 第一張：左側沒有鄰卡 → 左邊不外吃（片頭留給 head_trim）
    _case([[0.0, 4.0]], 0.15),
    # 最後一張：右側沒有鄰卡 → 右邊不外吃
    _case([[20.0, 24.0]], 0.15),
    # 影片模式的句中剪段（不對齊任何卡邊界）
    _case([[5.25, 6.5]], 0.15),
    # 反置 + 零長度：正規化要一致
    _case([[8.0, 4.0], [9.0, 9.0], [12.5, 14.0]], 0.15),
    # 全空
    _case([], 0.15),
    # 沒有卡表（空集）：兩側都沒有鄰卡
    _case([[1.0, 2.0]], 0.3, cards=[]),
    # 延伸後互相重疊 → 再合併一次
    _case([[4.0, 8.0], [12.5, 14.0]], 3.0),
    # ★ 重疊卡：刪 #2，區間本身要夾到保留卡 #3 的頭（7.50），pad 只能往左吃到 6.65
    _case([[7.35, 8.25]], 0.4, cards=OVERLAP_CARDS),
    # ★ 同上但 pad=0：夾制與 pad 無關（pad 只管延伸）
    _case([[7.35, 8.25]], 0.0, cards=OVERLAP_CARDS),
    # ★ 連刪兩張重疊卡：兩張都不是保留卡 → 互相不夾，照樣併成整段
    _case([[7.35, 8.25], [7.5, 10.15]], 0.15, cards=OVERLAP_CARDS),
    # ★ foreign cut 落在兩張保留卡的重疊區：不是刪卡產生的 → 邊界原樣，不夾
    _case([[7.4, 7.6]], 0.15, cards=OVERLAP_CARDS),
    # ★ 被兩張保留卡夾殺：夾完長度歸零 → 丟掉這段（不剪）
    _case([[2.0, 2.5]], 0.15, cards=SANDWICH_CARDS),
    _case([[2.0, 2.5]], 0.0, cards=SANDWICH_CARDS),
]


def test_fixed_cases_match_backend(tmp_path):
    """手寫情境：前端結果與後端 _pad_and_merge_cuts 逐位元相同。"""
    js = _js_batch(FIXED_CASES, tmp_path)
    for i, case in enumerate(FIXED_CASES):
        assert js[i] == _py(case), f"case #{i} 不一致：JS={js[i]} PY={_py(case)}｜{case}"


def test_random_cases_match_backend(tmp_path):
    """隨機情境差分（固定 seed，可重現）：任一條規則漂移都會紅。"""
    rnd = random.Random(20260925)
    cases = []
    for _ in range(300):
        n = rnd.randint(2, 7)
        t = 0.0
        cards = []
        for _ in range(n):
            t += round(rnd.uniform(0, 1.5), 2)   # 停頓（0 = flush 相鄰）
            dur = round(rnd.uniform(0.3, 3.0), 2)
            cards.append({"start": round(t, 2), "end": round(t + dur, 2)})
            t += dur
            if rnd.random() < 0.25:
                # 下一張的頭往回咬進這一張（逐字時間戳重疊）→ 壓夾制那條規則
                t = max(0.0, t - round(rnd.uniform(0.05, 0.8), 2))
        intervals = []
        for c in cards:
            if rnd.random() < 0.4:
                intervals.append([c["start"], c["end"]])
        if rnd.random() < 0.3:  # 摻一段不對齊卡邊界的影片模式剪段
            a = round(rnd.uniform(0, max(t - 0.5, 0.5)), 2)
            intervals.append([a, round(a + rnd.uniform(0.1, 1.2), 2)])
        cases.append(_case(sorted(intervals), rnd.choice([0.0, 0.15, 0.5, 2.0]), cards))

    js = _js_batch(cases, tmp_path)
    for i, case in enumerate(cases):
        assert js[i] == _py(case), f"random #{i} 不一致：JS={js[i]} PY={_py(case)}｜{case}"


@pytest.mark.parametrize("pad", [0.0, 0.15, 1.0])
def test_preview_intervals_equal_assemble_output(tmp_path, pad):
    """端到端形狀：前端預覽拿到的區間 == assemble 真的會剪掉的區間（含 foreign cut）。"""
    from podcast_toolkit import assemble

    v2 = [
        {"idx": i + 1, "start": c["start"], "end": c["end"], "text": "x"}
        for i, c in enumerate(CARDS)
    ]
    deleted = [[4.0, 8.0], [9.0, 12.0]]
    foreign = [[20.5, 21.0]]
    js = _js_batch([_case(sorted(deleted + foreign), pad)], tmp_path)[0]
    backend = assemble.cut_intervals_from_cfg(
        {"cuts": deleted + foreign, "cut_pad": pad}, v2
    )
    assert js == [[s, e] for s, e in backend]
