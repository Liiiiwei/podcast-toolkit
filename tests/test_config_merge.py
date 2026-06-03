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


# --- T23a：cameras / camera_sync_offset / audio schema ---


def test_merge_cameras_from_dict():
    """cameras dict 直接傳入要保留兩個鏡頭。"""
    cfg = config.merge(
        DEFAULTS,
        {"cameras": {"a": "01_母帶/cam_a.mp4", "b": "01_母帶/cam_b.mp4"}},
    )
    assert cfg["cameras"] == {"a": "01_母帶/cam_a.mp4", "b": "01_母帶/cam_b.mp4"}


def test_merge_cameras_legacy_main_video_becomes_a():
    """舊 episode.yaml 只有 main_video，自動視為 cameras.a（單機模式）。"""
    cfg = config.merge(DEFAULTS, {"main_video": "01_母帶/{name}.mp4"})
    assert cfg["cameras"] == {"a": "01_母帶/{name}.mp4"}


def test_merge_cameras_dict_overrides_main_video():
    """同時有 cameras 和 main_video 時，cameras 贏。"""
    cfg = config.merge(
        DEFAULTS,
        {
            "main_video": "01_母帶/{name}.mp4",
            "cameras": {"a": "01_母帶/cam_a.mp4", "b": "01_母帶/cam_b.mp4"},
        },
    )
    assert cfg["cameras"] == {"a": "01_母帶/cam_a.mp4", "b": "01_母帶/cam_b.mp4"}


def test_merge_cameras_missing_returns_empty_dict():
    """什麼都沒有時 cameras 是空 dict，由下游 raise。"""
    cfg = config.merge(DEFAULTS, {})
    assert cfg["cameras"] == {}


def test_merge_camera_sync_offset_default_empty():
    cfg = config.merge(DEFAULTS, {})
    assert cfg["camera_sync_offset"] == {}


def test_merge_camera_sync_offset_preserved():
    """L3 fallback：手填 b 相對於 a 的位移秒數。"""
    cfg = config.merge(DEFAULTS, {"camera_sync_offset": {"b": 0.42}})
    assert cfg["camera_sync_offset"] == {"b": 0.42}


def test_merge_audio_default_none():
    """沒設 audio 時是 None，表示沿用鏡頭原音。"""
    cfg = config.merge(DEFAULTS, {})
    assert cfg["audio"] is None


def test_merge_audio_preserved():
    """有 audio 時保留 stereo-mix 路徑 + 對齊參考。"""
    cfg = config.merge(
        DEFAULTS,
        {"audio": {"main": "01_母帶/stereo.wav", "sync_ref": "a", "offset_sec": 0.0}},
    )
    assert cfg["audio"] == {
        "main": "01_母帶/stereo.wav",
        "sync_ref": "a",
        "offset_sec": 0.0,
    }
