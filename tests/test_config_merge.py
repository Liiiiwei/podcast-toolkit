"""驗 config.merge 對新欄位 crop / deletions 的行為。"""
from podcast_toolkit import config


DEFAULTS = {
    "resegment": {"min_chars": 8},
    "subtitle_style": {"font_size": 28},
    "assets": {"intro": "x"},
    "encode": {"crf": 23},
    "common_fixes": [],
}


def test_merge_crop_missing_returns_none():
    cfg = config.merge(DEFAULTS, {"name": "t"})
    assert cfg["crop"] is None


def test_merge_crop_present_is_preserved():
    cfg = config.merge(
        DEFAULTS,
        {"name": "t", "crop": {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}},
    )
    assert cfg["crop"] == {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}


def test_merge_deletions_missing_returns_empty_list():
    cfg = config.merge(DEFAULTS, {"name": "t"})
    assert cfg["deletions"] == []


def test_merge_deletions_present_preserved_as_list():
    cfg = config.merge(DEFAULTS, {"name": "t", "deletions": [2, 4, 7]})
    assert cfg["deletions"] == [2, 4, 7]
