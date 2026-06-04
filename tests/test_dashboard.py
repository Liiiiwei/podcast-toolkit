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
