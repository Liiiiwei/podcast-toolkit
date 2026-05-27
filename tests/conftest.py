"""pytest fixtures：建臨時 episode 資料夾。"""
import shutil
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
    for sub in ("01_母帶", "02_片頭片尾", "03_成品", "04_工作檔"):
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
