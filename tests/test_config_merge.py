"""驗 config.merge 對 crop_yt / crop_reels / deletions 的行為。"""
from podcast_toolkit import config


DEFAULTS = {
    "resegment": {"min_chars": 8},
    "subtitle_style": {"font_size": 28},
    "assets": {"intro": "x"},
    "encode": {"crf": 23},
    "common_fixes": [],
}


def test_merge_crop_yt_missing_returns_none():
    cfg = config.merge(DEFAULTS, {"name": "t"})
    assert cfg["crop_yt"] is None


def test_merge_crop_yt_present_is_preserved():
    cfg = config.merge(
        DEFAULTS,
        {"name": "t", "crop_yt": {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}},
    )
    assert cfg["crop_yt"] == {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}


def test_merge_deletions_missing_returns_empty_list():
    cfg = config.merge(DEFAULTS, {"name": "t"})
    assert cfg["deletions"] == []


def test_merge_deletions_present_preserved_as_list():
    cfg = config.merge(DEFAULTS, {"name": "t", "deletions": [2, 4, 7]})
    assert cfg["deletions"] == [2, 4, 7]


def test_merge_crop_yt_and_reels():
    episode = {
        "crop_yt": {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0},
        "crop_reels": {"x": 0.3, "y": 0.0, "width": 0.4, "height": 1.0},
    }
    cfg = config.merge(DEFAULTS, episode)
    assert cfg["crop_yt"] == {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}
    assert cfg["crop_reels"] == {"x": 0.3, "y": 0.0, "width": 0.4, "height": 1.0}
    assert cfg.get("crop") is None  # 舊欄位不再透出


def test_merge_legacy_crop_migrated_to_crop_yt():
    """舊 episode.yaml 只有 crop，自動視為 crop_yt。"""
    episode = {"crop": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 0.5625}}
    cfg = config.merge(DEFAULTS, episode)
    assert cfg["crop_yt"] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 0.5625}
    assert cfg["crop_reels"] is None
