"""刪段時間版（cuts）：cfg 載入 / 遷移、字幕過濾、與舊 idx 路徑的等價。"""
from pathlib import Path

from podcast_toolkit import assemble, srt_io
from podcast_toolkit.segment_plan import build_segment_plan


def _cards(*rows):
    return [{"idx": i, "start": s, "end": e, "text": "x"} for i, s, e in rows]


CARDS = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0))


def test_cuts_new_format_list_pairs():
    cfg = {"cuts": [[4.0, 8.0]]}
    assert assemble.cut_intervals_from_cfg(cfg, CARDS) == [(4.0, 8.0)]


def test_cuts_new_format_dicts():
    cfg = {"cuts": [{"start": 4.0, "end": 8.0}, {"start": 1.0, "end": 2.0}]}
    # 回傳依 start 排序
    assert assemble.cut_intervals_from_cfg(cfg, CARDS) == [(1.0, 2.0), (4.0, 8.0)]


def test_legacy_deletions_idx_migrated_via_cards():
    """舊 deletions[idx] → 用 cards 換算成時間區間（自動遷移讀取）。"""
    cfg = {"deletions": [2]}
    assert assemble.cut_intervals_from_cfg(cfg, CARDS) == [(4.0, 8.0)]


def test_cuts_takes_priority_over_legacy_deletions():
    cfg = {"cuts": [[1.0, 2.0]], "deletions": [3]}
    assert assemble.cut_intervals_from_cfg(cfg, CARDS) == [(1.0, 2.0)]


def test_deletions_override_empty_means_no_cuts():
    """overlay「保留全部內容」傳 [] → 無刪段，蓋過 cfg。"""
    cfg = {"deletions": [2], "cuts": [[4.0, 8.0]]}
    assert assemble.cut_intervals_from_cfg(cfg, CARDS, deletions_override=[]) == []


def test_deletions_override_idx_list():
    cfg = {"cuts": [[1.0, 2.0]]}
    assert assemble.cut_intervals_from_cfg(cfg, CARDS, deletions_override=[3]) == [(8.0, 12.0)]


def test_filter_srt_by_intervals(tmp_path: Path):
    src = tmp_path / "a.srt"
    src.write_text(srt_io.serialize(CARDS), encoding="utf-8")
    dst = tmp_path / "b.srt"
    assemble.filter_srt_by_intervals(src, dst, [(4.0, 8.0)])
    kept = srt_io.parse(dst.read_text(encoding="utf-8"))
    texts_starts = [(c["start"]) for c in kept]
    # 卡2 (start 4.0) 落在 (4,8) → 移除；卡1、卡3 保留
    assert texts_starts == [0.0, 8.0]


def test_equivalence_legacy_deletions_vs_cuts():
    """同一刪段：舊 idx 路徑 與 時間版 cuts 路徑 → segment plan 一字不差。"""
    # 舊：deletions=[2] 經 cut_intervals_from_cfg 換算
    cuts_from_legacy = assemble.cut_intervals_from_cfg({"deletions": [2]}, CARDS)
    segs_legacy = build_segment_plan(
        cut_intervals=cuts_from_legacy, cam_transitions=[], main_dur=12.0,
    )
    # 新：直接給等價時間區間
    segs_cuts = build_segment_plan(
        cut_intervals=[(4.0, 8.0)], cam_transitions=[], main_dur=12.0,
    )
    assert segs_legacy == segs_cuts == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "a", "start": 8.0, "end": 12.0},
    ]
