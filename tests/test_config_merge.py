"""驗 config.merge 對 crop_yt / crop_reels / deletions 的行為。"""
from podcast_toolkit import config


DEFAULTS = {
    "resegment": {"min_chars": 8},
    "subtitle_style": {"font_size": 28},
    "assets": {"intro": "x"},
    "encode": {"crf": 23},
    "common_fixes": [],
    "per_mic": {"vad_threshold": 0.02, "vad_min_speech_sec": 0.3, "vad_pad_sec": 0.15},
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


# --- subtitle_style_reels：Reels 專用字幕風格分離 ---


def test_merge_subtitle_style_reels_falls_back_to_subtitle_style_when_missing():
    """defaults 沒給 subtitle_style_reels → 回退到 subtitle_style 整組。"""
    cfg = config.merge(DEFAULTS, {})
    # DEFAULTS 只有 subtitle_style: {font_size: 28}
    assert cfg["subtitle_style_reels"]["font_size"] == 28


def test_merge_subtitle_style_reels_from_defaults_overrides_subtitle_style():
    """defaults 有 subtitle_style_reels → reels 用該值；YT 仍用 subtitle_style。"""
    defaults_with_reels = {
        **DEFAULTS,
        "subtitle_style_reels": {"font_size": 80, "margin_v": 320},
    }
    cfg = config.merge(defaults_with_reels, {})
    assert cfg["subtitle_style"]["font_size"] == 28  # YT 不變
    assert cfg["subtitle_style_reels"]["font_size"] == 80  # Reels 用新值
    assert cfg["subtitle_style_reels"]["margin_v"] == 320


def test_merge_subtitle_style_reels_episode_override_partial():
    """episode 只覆蓋部分欄位（font_size），其它沿用 defaults.subtitle_style_reels。"""
    defaults_with_reels = {
        **DEFAULTS,
        "subtitle_style_reels": {"font_size": 80, "margin_v": 320, "outline": 3},
    }
    cfg = config.merge(
        defaults_with_reels,
        {"subtitle_style_reels": {"font_size": 96}},
    )
    assert cfg["subtitle_style_reels"]["font_size"] == 96   # episode 覆寫
    assert cfg["subtitle_style_reels"]["margin_v"] == 320   # 沿用 defaults
    assert cfg["subtitle_style_reels"]["outline"] == 3      # 沿用 defaults


def test_merge_subtitle_style_episode_override_propagates_to_reels():
    """episode 改 subtitle_style.font_size → reels 也跟著（base 共用），
    但 episode 若有 subtitle_style_reels 會贏。"""
    cfg = config.merge(
        DEFAULTS,
        {"subtitle_style": {"font_size": 36}},
    )
    # 沒有獨立的 reels override，base 改了 reels 也跟著
    assert cfg["subtitle_style_reels"]["font_size"] == 36


def test_merge_reels_clips_missing_returns_empty_list():
    cfg = config.merge(DEFAULTS, {"name": "t"})
    assert cfg["reels_clips"] == []


def test_merge_reels_clips_preserved():
    episode = {
        "reels_clips": [
            {"name": "hook1", "start_card": 5, "end_card": 12},
            {"name": "punchline", "start_card": 80, "end_card": 95},
        ]
    }
    cfg = config.merge(DEFAULTS, episode)
    assert cfg["reels_clips"] == [
        {"name": "hook1", "start_card": 5, "end_card": 12},
        {"name": "punchline", "start_card": 80, "end_card": 95},
    ]


def test_merge_subtitle_style_reels_wins_over_subtitle_style_in_episode():
    """episode 同時改 subtitle_style 和 subtitle_style_reels → reels 取後者。"""
    cfg = config.merge(
        DEFAULTS,
        {
            "subtitle_style": {"font_size": 36},
            "subtitle_style_reels": {"font_size": 96},
        },
    )
    assert cfg["subtitle_style"]["font_size"] == 36
    assert cfg["subtitle_style_reels"]["font_size"] == 96


# --- T31a：mics / per_mic schema（分軌轉錄）---


def test_merge_mics_missing_returns_empty_dict():
    """沒設 mics → 空 dict，呼叫端 fallback 走混音軌路線（向後相容）。"""
    cfg = config.merge(DEFAULTS, {})
    assert cfg["mics"] == {}


def test_merge_mics_preserved_as_dict():
    """設了 mics 要原樣保留兩支或三支 mic 的路徑。"""
    episode = {
        "mics": {
            "a": "01_母帶/{name}_micA.wav",
            "b": "01_母帶/{name}_micB.wav",
            "c": "01_母帶/{name}_micC.wav",
        }
    }
    cfg = config.merge(DEFAULTS, episode)
    assert cfg["mics"] == {
        "a": "01_母帶/{name}_micA.wav",
        "b": "01_母帶/{name}_micB.wav",
        "c": "01_母帶/{name}_micC.wav",
    }


def test_merge_per_mic_defaults_used_when_episode_missing():
    """per_mic 沒設時走 defaults 預設值。"""
    cfg = config.merge(DEFAULTS, {})
    assert cfg["per_mic"]["vad_threshold"] == 0.02
    assert cfg["per_mic"]["vad_min_speech_sec"] == 0.3
    assert cfg["per_mic"]["vad_pad_sec"] == 0.15


def test_merge_per_mic_episode_overrides_defaults_per_key():
    """episode 只覆寫部分 per_mic 欄位 → 其餘走 defaults。"""
    cfg = config.merge(DEFAULTS, {"per_mic": {"vad_threshold": 0.05}})
    assert cfg["per_mic"]["vad_threshold"] == 0.05  # overridden
    assert cfg["per_mic"]["vad_min_speech_sec"] == 0.3  # default
    assert cfg["per_mic"]["vad_pad_sec"] == 0.15  # default
