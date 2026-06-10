"""Reels 片段截取（assemble._original_to_mp4_time）時間軸換算測試。"""
from podcast_toolkit.assemble import _original_to_mp4_time


def test_no_deletions_identity():
    assert _original_to_mp4_time(120.0, []) == 120.0


def test_head_trim_only_shifts_back():
    intervals = [(0.0, 5.0)]
    assert _original_to_mp4_time(120.0, intervals) == 115.0


def test_head_trim_plus_middle_deletion_accumulates():
    # head_trim 5s + 一段刪 50-53s（3s）→ t=120 變 mp4 t=112
    intervals = [(0.0, 5.0), (50.0, 53.0)]
    assert _original_to_mp4_time(120.0, intervals) == 112.0


def test_t_src_before_any_deletion_only_subtracts_head():
    intervals = [(0.0, 5.0), (50.0, 53.0)]
    assert _original_to_mp4_time(20.0, intervals) == 15.0


def test_t_src_inside_deletion_clamps_to_segment_start():
    # 50-53s 被刪，要 51.5s → 應該回 mp4 time = 51.5 - 5(head) - (51.5-50) = 45
    intervals = [(0.0, 5.0), (50.0, 53.0)]
    assert _original_to_mp4_time(51.5, intervals) == 45.0


def test_tail_trim_doesnt_affect_earlier_times():
    intervals = [(0.0, 5.0), (1200.0, 1210.0)]  # head_trim 5s + tail_trim 10s
    assert _original_to_mp4_time(600.0, intervals) == 595.0


def test_multiple_deletions_sum():
    intervals = [(0.0, 5.0), (50.0, 53.0), (100.0, 110.0)]  # 5 + 3 + 10 = 18
    assert _original_to_mp4_time(200.0, intervals) == 200.0 - 18.0


def test_never_returns_negative():
    intervals = [(0.0, 100.0)]
    # t_src 在 deletion 內 → 不應該變成負值
    assert _original_to_mp4_time(50.0, intervals) == 0.0
