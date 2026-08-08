"""dashboard 集數清單對「單一壞資料夾」容錯測試。

情境（2026-08-08 feedback-signals-ux 第 3 節）：~/Downloads 出現一個
沒有進入權限的資料夾（chmod 000），(child / "episode.yaml").is_file()
拋 PermissionError 逸出 list_episodes → /api/episodes 500 → 整份
dashboard 清單死掉。要求：壞資料夾跳過但失敗不靜默（進 warnings）。
"""
import os
from pathlib import Path

import pytest

from podcast_toolkit.web import dashboard
from tests.test_dashboard import _make_initialized_episode

# root 使用者不受 chmod 000 限制，權限類測試會失真
needs_nonroot = pytest.mark.skipif(
    os.geteuid() == 0, reason="root 不受 chmod 000 限制，無法模擬權限錯"
)


@needs_nonroot
def test_list_episodes_tolerates_unreadable_and_bad_yaml_folders(tmp_path: Path):
    """1 好集＋1 權限 000 資料夾＋1 壞 yaml 集：回好集、壞的進 warnings／broken、不拋例外。"""
    root = tmp_path / "Downloads"
    root.mkdir()

    good = _make_initialized_episode(root, "20260601 好集")
    (good / "01_母帶" / "好集.mp4").write_bytes(b"X")

    bad_yaml = root / "20260602 壞集"
    bad_yaml.mkdir()
    (bad_yaml / "episode.yaml").write_text("a: [unclosed", encoding="utf-8")

    no_access = root / "no_access"
    no_access.mkdir()
    os.chmod(no_access, 0o000)
    try:
        result = dashboard.list_episodes(roots=[str(root)], recent=[])
    finally:
        # 不復原的話 pytest 清 tmp_path 會炸
        os.chmod(no_access, 0o755)

    paths = {e["path"]: e for e in result["episodes"]}
    # 好集正常列出
    assert str(good) in paths
    assert paths[str(good)]["stage"] == "needs_transcribe"
    # 壞 yaml 集列出但標 broken（既有慣例），不上拋
    assert str(bad_yaml) in paths
    assert paths[str(bad_yaml)]["stage"] == "broken"
    # 權限錯資料夾：不在清單、但失敗不靜默 → warnings 有對應條目
    assert str(no_access) not in paths
    assert any("no_access" in w for w in result["warnings"]), result["warnings"]


@needs_nonroot
def test_list_episodes_recent_tolerates_unreadable_folder(tmp_path: Path):
    """recent 指到權限 000 資料夾：跳過＋warnings，不拋例外。"""
    no_access = tmp_path / "no_access"
    no_access.mkdir()
    os.chmod(no_access, 0o000)
    try:
        result = dashboard.list_episodes(roots=[], recent=[str(no_access)])
    finally:
        os.chmod(no_access, 0o755)

    assert result["episodes"] == []
    assert any("no_access" in w for w in result["warnings"]), result["warnings"]


@needs_nonroot
def test_stage_broken_on_unreadable_dir(tmp_path: Path):
    """episode_stage 對權限 000 資料夾標 broken 而非上拋（延遲讀也蓋到）。"""
    no_access = tmp_path / "no_access"
    no_access.mkdir()
    os.chmod(no_access, 0o000)
    try:
        assert dashboard.episode_stage(no_access) == "broken"
    finally:
        os.chmod(no_access, 0o755)


def test_stage_broken_on_bad_yaml(tmp_path: Path):
    """episode.yaml 解析失敗 → broken，不上拋。"""
    ep = tmp_path / "20260603 壞集"
    ep.mkdir()
    (ep / "episode.yaml").write_text("a: [unclosed", encoding="utf-8")
    assert dashboard.episode_stage(ep) == "broken"
