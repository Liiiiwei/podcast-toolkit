"""dashboard.py 純函式測試。"""
from pathlib import Path

import pytest

from podcast_toolkit.web import dashboard


def test_stage_broken_when_no_episode_yaml(tmp_path: Path):
    folder = tmp_path / "no_yaml"
    folder.mkdir()
    assert dashboard.episode_stage(folder) == "broken"


def test_stage_empty_when_no_main_video(tmp_episode_dir: Path):
    # fixture 已預植 _final_v2.srt，但因為沒 main_video，episode_stage 會先回 empty
    assert dashboard.episode_stage(tmp_episode_dir) == "empty"


def test_stage_needs_transcribe(tmp_episode_dir: Path):
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"X")
    # 移除 fixture 預植的 _final_v2.srt
    (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").unlink()
    assert dashboard.episode_stage(tmp_episode_dir) == "needs_transcribe"


def test_stage_needs_assemble(tmp_episode_dir: Path):
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"X")
    # fixture 已預植 _final_v2.srt → 不再寫入
    assert dashboard.episode_stage(tmp_episode_dir) == "needs_assemble"


def test_stage_done_when_output_exists(tmp_episode_dir: Path):
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"X")
    # fixture 已預植 _final_v2.srt
    (tmp_episode_dir / "03_成品" / "測試集_YT完整版.mp4").write_bytes(b"OUT")
    assert dashboard.episode_stage(tmp_episode_dir) == "done"


def test_load_recent_returns_empty_on_missing(tmp_path: Path):
    cfg = tmp_path / "config.json"
    assert dashboard.load_recent(cfg) == []


def test_load_recent_returns_empty_on_bad_json(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text("not json", encoding="utf-8")
    assert dashboard.load_recent(cfg) == []


def test_save_then_load_roundtrip(tmp_path: Path):
    cfg = tmp_path / "config.json"
    dashboard.save_recent(cfg, ["/a", "/b"])
    assert dashboard.load_recent(cfg) == ["/a", "/b"]


def test_save_preserves_other_config_keys(tmp_path: Path):
    """save_recent 不能炸掉 config.json 內既有的 xai_api_key 等欄位。"""
    import json
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"xai_api_key": "K"}), encoding="utf-8")
    dashboard.save_recent(cfg, ["/a"])
    loaded = json.loads(cfg.read_text(encoding="utf-8"))
    assert loaded["xai_api_key"] == "K"
    assert loaded["recent_episodes"] == ["/a"]


def test_add_recent_prepends_and_dedups(tmp_path: Path):
    cfg = tmp_path / "config.json"
    dashboard.save_recent(cfg, ["/a", "/b"])
    dashboard.add_recent(cfg, "/b")  # 已存在 → 移到最前
    assert dashboard.load_recent(cfg) == ["/b", "/a"]
    dashboard.add_recent(cfg, "/c")  # 新的 → 加最前
    assert dashboard.load_recent(cfg) == ["/c", "/b", "/a"]


def test_add_recent_caps_at_20(tmp_path: Path):
    cfg = tmp_path / "config.json"
    for i in range(25):
        dashboard.add_recent(cfg, f"/p{i}")
    recent = dashboard.load_recent(cfg)
    assert len(recent) == 20
    assert recent[0] == "/p24"  # 最新在最前
    assert recent[-1] == "/p5"  # 最舊的 5 個被砍掉


def test_save_atomic(tmp_path: Path):
    """save 走 .tmp + rename，中間 .tmp 不能殘留。"""
    cfg = tmp_path / "config.json"
    dashboard.save_recent(cfg, ["/a"])
    assert not (tmp_path / "config.json.tmp").exists()
