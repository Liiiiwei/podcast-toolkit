"""自動去頭去尾(autotrim.py + silencedetect 尾段)測試。

silencedetect 的 tail 解析用假 stderr 驗證;autotrim.run monkeypatch 掉實際 ffmpeg 偵測,
專注驗證「只補沒設過的值、尊重手動值、safe round-trip 不動其他欄位」。
"""
from __future__ import annotations

import yaml

from podcast_toolkit import autotrim, silencedetect
from podcast_toolkit.episode import Episode


def test_parse_duration():
    assert silencedetect.parse_duration("  Duration: 00:36:26.20, start: 0") == 36 * 60 + 26.2
    assert silencedetect.parse_duration("沒有時長資訊") == 0.0


def test_parse_tail_silence_trailing():
    s = (
        "  Duration: 00:10:00.00\n"
        "[silencedetect] silence_start: 595.0\n"
        "[silencedetect] silence_end: 600.0 | silence_duration: 5.0\n"
    )
    assert silencedetect.parse_tail_silence(s, silencedetect.parse_duration(s)) == 5.0


def test_parse_tail_silence_mid_only_returns_zero():
    # 靜音在中段、結尾有聲 → 不該誤判成尾段靜音
    s = (
        "  Duration: 00:10:00.00\n"
        "[silencedetect] silence_start: 100.0\n"
        "[silencedetect] silence_end: 105.0 | silence_duration: 5.0\n"
    )
    assert silencedetect.parse_tail_silence(s, silencedetect.parse_duration(s)) == 0.0


def test_parse_tail_silence_open_to_eof():
    # silence_start 開著沒對應 silence_end → 靜音延續到檔尾
    s = "  Duration: 00:10:00.00\n[silencedetect] silence_start: 580.0\n"
    assert silencedetect.parse_tail_silence(s, silencedetect.parse_duration(s)) == 20.0


def test_parse_tail_silence_no_duration_returns_zero():
    s = "[silencedetect] silence_start: 580.0\n"
    assert silencedetect.parse_tail_silence(s, 0.0) == 0.0


def _prep_video(ep_dir):
    """autotrim 會檢查 main_video 是否存在 → 放一個 stub 母帶。"""
    (ep_dir / "01_母帶" / "測試集.mp4").write_bytes(b"")


def test_autotrim_fills_missing_tail_keeps_other_keys(tmp_episode_dir, monkeypatch):
    _prep_video(tmp_episode_dir)
    yaml_path = tmp_episode_dir / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    data["head_trim_sec"] = 42.7           # 手動設過的 head
    data["deletions"] = [4, 7, 9]          # 其他欄位,不該被動到
    yaml_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                         encoding="utf-8")

    monkeypatch.setattr(autotrim, "detect_head_silence", lambda *a, **k: 99.0)
    monkeypatch.setattr(autotrim, "detect_tail_silence", lambda *a, **k: 12.3)

    changes = autotrim.run(Episode(tmp_episode_dir))
    assert changes == {"tail_trim_sec": 12.3}     # 只補沒設過的 tail

    after = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert after["head_trim_sec"] == 42.7          # 手動 head 沒被覆寫
    assert after["tail_trim_sec"] == 12.3
    assert after["deletions"] == [4, 7, 9]         # 其他欄位完好


def test_autotrim_force_overwrites_both(tmp_episode_dir, monkeypatch):
    _prep_video(tmp_episode_dir)
    yaml_path = tmp_episode_dir / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    data["head_trim_sec"] = 42.7
    yaml_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                         encoding="utf-8")

    monkeypatch.setattr(autotrim, "detect_head_silence", lambda *a, **k: 30.0)
    monkeypatch.setattr(autotrim, "detect_tail_silence", lambda *a, **k: 8.0)

    changes = autotrim.run(Episode(tmp_episode_dir), force=True)
    assert changes == {"head_trim_sec": 30.0, "tail_trim_sec": 8.0}


def test_autotrim_ignores_silence_below_min(tmp_episode_dir, monkeypatch):
    _prep_video(tmp_episode_dir)
    monkeypatch.setattr(autotrim, "detect_head_silence", lambda *a, **k: 0.3)
    monkeypatch.setattr(autotrim, "detect_tail_silence", lambda *a, **k: 0.0)
    assert autotrim.run(Episode(tmp_episode_dir)) == {}      # 太短 → 不標


def test_autotrim_missing_video_returns_empty(tmp_episode_dir):
    # 沒放母帶 stub → main_video 不存在 → 回空,不爆
    assert autotrim.run(Episode(tmp_episode_dir)) == {}
