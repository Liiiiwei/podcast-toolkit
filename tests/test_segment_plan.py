"""雙鏡頭分段計畫：把字幕卡 + 刪除/trim + **時間版鏡頭切換點** 轉成 [(cam, start, end)] 區段表。

純函式，不碰檔案，方便單獨測。鏡頭已與字幕脫鉤：cam_transitions 是 [{"t", "cam"}]。
"""
from podcast_toolkit.segment_plan import build_segment_plan


def _cards(*rows):
    """(idx, start, end) → cards dict list"""
    return [{"idx": i, "start": s, "end": e, "text": ""} for i, s, e in rows]


def test_no_mapping_returns_single_a_segment():
    """沒有任何鏡頭切換 → 整段都走 cam a。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cam_transitions=[], main_dur=10.0,
    )
    assert segs == [{"cam": "a", "start": 0.0, "end": 10.0}]


def test_explicit_b_mid_video_splits_into_two_segments():
    """4.0s 切到 b → 兩段。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cam_transitions=[{"t": 4.0, "cam": "b"}], main_dur=10.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "b", "start": 4.0, "end": 10.0},
    ]


def test_carry_forward_after_explicit_mark():
    """4.0s 切 b 之後沒再切 → 一路繼承 b 到結尾。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0), (4, 12.0, 16.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cam_transitions=[{"t": 4.0, "cam": "b"}], main_dur=16.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "b", "start": 4.0, "end": 16.0},
    ]


def test_switch_back_to_a_creates_three_segments():
    """b → a 再切回來，要三段。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0))
    segs = build_segment_plan(
        cards=cards, deletions=[],
        cam_transitions=[{"t": 4.0, "cam": "b"}, {"t": 8.0, "cam": "a"}], main_dur=12.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "b", "start": 4.0, "end": 8.0},
        {"cam": "a", "start": 8.0, "end": 12.0},
    ]


def test_redundant_same_cam_transition_does_not_split():
    """4.0s 切 a，但前面已是 a → merge 會收回成單段（不該留下兩段）。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cam_transitions=[{"t": 4.0, "cam": "a"}], main_dur=10.0,
    )
    assert segs == [{"cam": "a", "start": 0.0, "end": 10.0}]


def test_transition_not_on_card_boundary_still_cuts():
    """切換點是純時間、與字幕卡脫鉤 → 落在卡中間(5.0s)也照切。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cam_transitions=[{"t": 5.0, "cam": "b"}], main_dur=10.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 5.0},
        {"cam": "b", "start": 5.0, "end": 10.0},
    ]


def test_deletion_splits_segment():
    """刪掉 (4,8) → 留下 (0,4) 和 (8, main)。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0))
    segs = build_segment_plan(
        cards=cards, deletions=[2], cam_transitions=[], main_dur=12.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "a", "start": 8.0, "end": 12.0},
    ]


def test_head_trim_drops_initial_portion():
    """head_trim_sec=2 → 從 2.0 開始。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cam_transitions=[], main_dur=10.0,
        head_trim_sec=2.0,
    )
    assert segs == [{"cam": "a", "start": 2.0, "end": 10.0}]


def test_tail_trim_drops_ending():
    """tail_trim_sec=3 → 砍到 main_dur - 3。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cam_transitions=[], main_dur=10.0,
        tail_trim_sec=3.0,
    )
    assert segs == [{"cam": "a", "start": 0.0, "end": 7.0}]


def test_transition_inside_deleted_region_is_dropped():
    """切換點落在被刪區間 (4,8) 內 → 無意義，丟掉；後面依然 carry 上一個 a。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0))
    segs = build_segment_plan(
        cards=cards, deletions=[2], cam_transitions=[{"t": 4.0, "cam": "b"}], main_dur=12.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "a", "start": 8.0, "end": 12.0},
    ]


def test_empty_cards_returns_single_a_segment():
    """沒字幕卡也要能跑（保險），全段 a。"""
    segs = build_segment_plan(
        cards=[], deletions=[], cam_transitions=[], main_dur=10.0,
    )
    assert segs == [{"cam": "a", "start": 0.0, "end": 10.0}]


def test_head_trim_with_camera_switch_after():
    """head_trim 蓋掉前 2s，4.0s 切 b → trim 後第一段 a(2,4)，再 b(4,10)。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cam_transitions=[{"t": 4.0, "cam": "b"}], main_dur=10.0,
        head_trim_sec=2.0,
    )
    assert segs == [
        {"cam": "a", "start": 2.0, "end": 4.0},
        {"cam": "b", "start": 4.0, "end": 10.0},
    ]


def test_default_cam_can_be_overridden():
    """預設 cam 可改 b（罕見但要支援）。"""
    cards = _cards((1, 0.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cam_transitions=[], main_dur=10.0,
        default_cam="b",
    )
    assert segs == [{"cam": "b", "start": 0.0, "end": 10.0}]
