"""雙鏡頭分段計畫（**純時間版**）：刪段(時間區間) + trim + 鏡頭切換點(時間) → [(cam,start,end)]。

不碰檔案、不需要字幕卡，純時間數學。鏡頭=cam_transitions[{t,cam}]、刪段=cut_intervals[(start,end)]。
"""
from podcast_toolkit.segment_plan import build_segment_plan


def test_no_cuts_no_transitions_single_a_segment():
    """沒刪段、沒鏡頭切換 → 整段 cam a。"""
    segs = build_segment_plan(cut_intervals=[], cam_transitions=[], main_dur=10.0)
    assert segs == [{"cam": "a", "start": 0.0, "end": 10.0}]


def test_transition_mid_splits_into_two():
    """4.0s 切 b → 兩段。"""
    segs = build_segment_plan(
        cut_intervals=[], cam_transitions=[{"t": 4.0, "cam": "b"}], main_dur=10.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "b", "start": 4.0, "end": 10.0},
    ]


def test_carry_forward_after_transition():
    """4.0s 切 b 之後沒再切 → 一路 b。"""
    segs = build_segment_plan(
        cut_intervals=[], cam_transitions=[{"t": 4.0, "cam": "b"}], main_dur=16.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "b", "start": 4.0, "end": 16.0},
    ]


def test_switch_back_to_a_three_segments():
    segs = build_segment_plan(
        cut_intervals=[],
        cam_transitions=[{"t": 4.0, "cam": "b"}, {"t": 8.0, "cam": "a"}], main_dur=12.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "b", "start": 4.0, "end": 8.0},
        {"cam": "a", "start": 8.0, "end": 12.0},
    ]


def test_redundant_same_cam_transition_does_not_split():
    """4.0s 切 a，前面已是 a → merge 收回單段。"""
    segs = build_segment_plan(
        cut_intervals=[], cam_transitions=[{"t": 4.0, "cam": "a"}], main_dur=10.0,
    )
    assert segs == [{"cam": "a", "start": 0.0, "end": 10.0}]


def test_cut_interval_splits_segment():
    """刪掉 (4,8) → 留 (0,4) 和 (8, main)。"""
    segs = build_segment_plan(
        cut_intervals=[(4.0, 8.0)], cam_transitions=[], main_dur=12.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "a", "start": 8.0, "end": 12.0},
    ]


def test_free_time_cut_not_on_card_boundary():
    """刪段是自由秒數（ruler 拖選），(3.5, 6.2) 照切，與任何字幕卡邊界無關。"""
    segs = build_segment_plan(
        cut_intervals=[(3.5, 6.2)], cam_transitions=[], main_dur=10.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 3.5},
        {"cam": "a", "start": 6.2, "end": 10.0},
    ]


def test_multiple_cuts():
    segs = build_segment_plan(
        cut_intervals=[(2.0, 3.0), (6.0, 7.0)], cam_transitions=[], main_dur=10.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 2.0},
        {"cam": "a", "start": 3.0, "end": 6.0},
        {"cam": "a", "start": 7.0, "end": 10.0},
    ]


def test_head_trim_drops_initial_portion():
    segs = build_segment_plan(
        cut_intervals=[], cam_transitions=[], main_dur=10.0, head_trim_sec=2.0,
    )
    assert segs == [{"cam": "a", "start": 2.0, "end": 10.0}]


def test_tail_trim_drops_ending():
    segs = build_segment_plan(
        cut_intervals=[], cam_transitions=[], main_dur=10.0, tail_trim_sec=3.0,
    )
    assert segs == [{"cam": "a", "start": 0.0, "end": 7.0}]


def test_transition_inside_cut_is_dropped():
    """切換點落在刪除區間 (4,8) 內 → 丟掉；後面 carry 上一個 a。"""
    segs = build_segment_plan(
        cut_intervals=[(4.0, 8.0)], cam_transitions=[{"t": 4.0, "cam": "b"}], main_dur=12.0,
    )
    assert segs == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "a", "start": 8.0, "end": 12.0},
    ]


def test_head_trim_with_camera_switch_after():
    segs = build_segment_plan(
        cut_intervals=[], cam_transitions=[{"t": 4.0, "cam": "b"}], main_dur=10.0,
        head_trim_sec=2.0,
    )
    assert segs == [
        {"cam": "a", "start": 2.0, "end": 4.0},
        {"cam": "b", "start": 4.0, "end": 10.0},
    ]


def test_default_cam_can_be_overridden():
    segs = build_segment_plan(
        cut_intervals=[], cam_transitions=[], main_dur=10.0, default_cam="b",
    )
    assert segs == [{"cam": "b", "start": 0.0, "end": 10.0}]
