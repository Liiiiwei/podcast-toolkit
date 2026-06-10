"""雙鏡頭分段計畫：把字幕卡 + 鏡頭對應 + 刪除/trim 轉成 [(cam, start, end)] 區段表。

純函式，不碰檔案，方便單獨測。
"""
from podcast_toolkit.segment_plan import build_segment_plan


def _cards(*rows):
    """(idx, start, end) → cards dict list"""
    return [{"idx": i, "start": s, "end": e, "text": ""} for i, s, e in rows]


def test_no_mapping_returns_single_a_segment():
    """沒有任何鏡頭對應 → 整段都走 cam a。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cameras_mapping={}, main_dur=10.0,
    )
    assert segs == [{"cam": "a", "start": 0.0, "end": 10.0}]


def test_explicit_b_mid_video_splits_into_two_segments():
    """卡 2 標 b → 第二張卡開始切到 b。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cameras_mapping={2: "b"}, main_dur=10.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "b", "start": 4.0, "end": 10.0},
    ]


def test_carry_forward_after_explicit_mark():
    """卡 2 標 b 之後沒再標 → 卡 3、4 都繼承 b。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0), (4, 12.0, 16.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cameras_mapping={2: "b"}, main_dur=16.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "b", "start": 4.0, "end": 16.0},
    ]


def test_switch_back_to_a_creates_three_segments():
    """b → a 再切回來，要三段。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cameras_mapping={2: "b", 3: "a"}, main_dur=12.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "b", "start": 4.0, "end": 8.0},
        {"cam": "a", "start": 8.0, "end": 12.0},
    ]


def test_explicit_a_on_already_a_card_does_not_split():
    """卡 2 顯式標 a，前面也是 a → 不該切兩段。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cameras_mapping={2: "a"}, main_dur=10.0,
    )
    assert segs == [{"cam": "a", "start": 0.0, "end": 10.0}]


def test_deletion_splits_segment():
    """刪掉卡 2 → 留下 (0,4) 和 (8, main)。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0))
    segs = build_segment_plan(
        cards=cards, deletions=[2], cameras_mapping={}, main_dur=12.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "a", "start": 8.0, "end": 12.0},
    ]


def test_head_trim_drops_initial_portion():
    """head_trim_sec=2 → 從 2.0 開始。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cameras_mapping={}, main_dur=10.0,
        head_trim_sec=2.0,
    )
    assert segs == [{"cam": "a", "start": 2.0, "end": 10.0}]


def test_tail_trim_drops_ending():
    """tail_trim_sec=3 → 砍到 main_dur - 3。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cameras_mapping={}, main_dur=10.0,
        tail_trim_sec=3.0,
    )
    assert segs == [{"cam": "a", "start": 0.0, "end": 7.0}]


def test_deleted_card_does_not_carry_camera_forward():
    """卡 2 標 b 但同時被刪 → 不影響後面卡（依然 carry 上一張 a）。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0))
    segs = build_segment_plan(
        cards=cards, deletions=[2], cameras_mapping={2: "b"}, main_dur=12.0,
    )
    # 卡 2 被刪 → b 對應失效 → 卡 3 依然 carry a
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "a", "start": 8.0, "end": 12.0},
    ]


def test_empty_cards_returns_single_a_segment():
    """沒字幕卡也要能跑（保險），全段 a。"""
    segs = build_segment_plan(
        cards=[], deletions=[], cameras_mapping={}, main_dur=10.0,
    )
    assert segs == [{"cam": "a", "start": 0.0, "end": 10.0}]


def test_head_trim_with_camera_switch_after():
    """head_trim 蓋掉卡 1，但 b 從卡 2 開始 → 從 trim 點就是 b（因為 a 整段被砍）。

    其實 trim 後第一段仍以 trim 點為起點；卡 2 在 trim 後 → 切點正常產生。
    """
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 10.0))
    segs = build_segment_plan(
        cards=cards, deletions=[], cameras_mapping={2: "b"}, main_dur=10.0,
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
        cards=cards, deletions=[], cameras_mapping={}, main_dur=10.0,
        default_cam="b",
    )
    assert segs == [{"cam": "b", "start": 0.0, "end": 10.0}]
