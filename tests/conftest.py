"""pytest fixtures：建臨時 episode 資料夾。"""
from pathlib import Path

import pytest
import yaml


SAMPLE_SRT = """\
1
00:00:00,000 --> 00:00:04,200
大家好歡迎來到我愛上班

2
00:00:04,200 --> 00:00:12,000
今天要聊的是過嗨乳牛這個議題

3
00:00:12,000 --> 00:00:14,000
呃那個

4
00:00:14,000 --> 00:00:22,000
我們從牠的飼料配方開始講起
"""


@pytest.fixture
def tmp_episode_dir(tmp_path: Path) -> Path:
    """建一個最小 episode 資料夾結構，回傳路徑。"""
    ep = tmp_path / "20260601 測試集"
    ep.mkdir()
    for sub in ("01_母帶", "03_成品", "04_工作檔"):
        (ep / sub).mkdir()

    (ep / "episode.yaml").write_text(
        yaml.safe_dump(
            {
                "date": 20260601,
                "name": "測試集",
                "main_video": "01_母帶/{name}.mp4",
                "main_srt": "01_母帶/{name}.srt",
                "fixes": [],
                "card_fixes": [],
                "force_break": [],
                "force_join": [],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    # 放一個 v2 srt 在 03_成品/
    (ep / "03_成品" / "測試集_final_v2.srt").write_text(SAMPLE_SRT, encoding="utf-8")

    return ep


@pytest.fixture
def tmp_episode_with_crops(tmp_episode_dir: Path) -> Path:
    """在 tmp_episode_dir 之上補 crop_yt + crop_reels 到 yaml。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "crop_yt:\n  x: 0.1\n  y: 0.0\n  width: 0.8\n  height: 1.0\n"
        + "crop_reels:\n  x: 0.3\n  y: 0.0\n  width: 0.4\n  height: 1.0\n",
        encoding="utf-8",
    )
    return tmp_episode_dir


@pytest.fixture
def tmp_episode_full(tmp_episode_dir: Path, monkeypatch) -> Path:
    """在 tmp_episode_dir 之上補齊 prepare_assembly 需要的檔案：
    - 01_母帶/{name}.mp4 stub（空檔，由 monkeypatch ffprobe_duration 蓋掉量測）
    - assemble.ffprobe_duration 回傳固定 100.0 秒
    - shutil.which 對 ffmpeg / ffprobe 都回傳 True（避免本機沒裝測試直接掛掉）
    """
    # 母帶 stub
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"")

    from podcast_toolkit import assemble as _asm

    monkeypatch.setattr(_asm, "ffprobe_duration", lambda _p: 100.0)
    monkeypatch.setattr(_asm.shutil, "which", lambda _name: "/usr/bin/" + _name)

    return tmp_episode_dir


@pytest.fixture
def tmp_episode_full_multicam(tmp_episode_full: Path) -> Path:
    """在 tmp_episode_full 之上補齊雙鏡頭資產：
    - 01_母帶/測試集_camB.mp4 stub
    - episode.yaml 補 cameras + camera_sync_offset
    - 03_成品/測試集_final_v2.cameras.json sidecar（卡 3 標 b → 其餘 carry-forward a）
    """
    (tmp_episode_full / "01_母帶" / "測試集_camB.mp4").write_bytes(b"")

    yaml_path = tmp_episode_full / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    data["cameras"] = {
        "a": "01_母帶/{name}.mp4",
        "b": "01_母帶/{name}_camB.mp4",
    }
    data["camera_sync_offset"] = {"b": 1.25}
    yaml_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    # sidecar：卡 3 切到 b（_v2.srt 已在 tmp_episode_dir 寫入）
    sidecar = tmp_episode_full / "03_成品" / "測試集_final_v2.cameras.json"
    sidecar.write_text('{"3": "b"}', encoding="utf-8")

    return tmp_episode_full
