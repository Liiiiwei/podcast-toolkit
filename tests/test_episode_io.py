"""web/episode_io：把 episode 資料夾轉成前端要的 JSON 狀態。"""
import yaml

from podcast_toolkit.episode import Episode
from podcast_toolkit.web import episode_io


def test_load_state_returns_name_and_cards(tmp_episode_dir):
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["name"] == "測試集"
    assert state["crop_yt"] is None
    assert state["crop_reels"] is None
    assert state["deletions"] == []
    assert len(state["cards"]) == 4
    assert state["cards"][0]["idx"] == 1
    assert state["cards"][0]["text"] == "大家好歡迎來到我愛上班"
    assert state["needs_transcribe"] is False


def test_load_state_returns_needs_transcribe_when_v2_missing(tmp_episode_dir):
    """新集還沒跑過 resegment（缺 _v2.srt）時，load_state 不應 raise，
    而是回 needs_transcribe=True + cards=[]，讓前端引導使用者去轉字幕。"""
    # 刪掉 _v2.srt 模擬新集情境
    (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").unlink()
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["name"] == "測試集"
    assert state["needs_transcribe"] is True
    assert state["cards"] == []
    # crop / deletions 仍正常從 yaml 讀
    assert state["crop_yt"] is None
    assert state["deletions"] == []


def test_load_state_includes_crop_and_deletions_from_yaml(tmp_episode_dir):
    # 改寫 yaml 加 crop_yt / deletions
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "crop_yt:\n  x: 0.1\n  y: 0.0\n  width: 0.8\n  height: 1.0\n"
        + "deletions: [3]\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["crop_yt"] == {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}
    assert state["deletions"] == [3]


def test_save_state_writes_crop_and_deletions_to_yaml(tmp_episode_dir):
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.9},
            "deletions": [2, 4],
            "cards": [],
        },
    )
    new_yaml = yaml.safe_load(
        (tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8")
    )
    assert new_yaml["crop_yt"] == {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.9}
    assert new_yaml["deletions"] == [2, 4]


def test_save_state_overwrites_v2_srt_with_card_text_overrides(tmp_episode_dir):
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None,
            "crop_reels": None,
            "deletions": [],
            "cards": [{"idx": 1, "text": "大家午安歡迎來到我愛上班"}],
        },
    )
    v2 = (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").read_text(encoding="utf-8")
    assert "大家午安" in v2
    assert "大家好歡迎" not in v2
    # 其他段未動
    assert "今天要聊的是過嗨乳牛" in v2


def test_save_state_removes_crop_when_none(tmp_episode_dir):
    # 先寫 crop_yt 進去
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "crop_yt:\n  x: 0.1\n  y: 0.0\n  width: 0.8\n  height: 1.0\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(ep, payload={"crop_yt": None, "crop_reels": None, "deletions": [], "cards": []})
    new_yaml = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert "crop_yt" not in new_yaml or new_yaml["crop_yt"] is None


def test_episode_output_reels_video(tmp_episode_dir):
    ep = Episode(tmp_episode_dir)
    out = ep.output_reels_video()
    assert out.name.endswith("_Reels.mp4")
    assert out.parent.name == "03_成品"


def test_episode_io_load_returns_crop_yt_and_reels(tmp_episode_with_crops):
    from podcast_toolkit.web import episode_io
    from podcast_toolkit.episode import Episode
    ep = Episode(tmp_episode_with_crops)
    state = episode_io.load_state(ep)
    assert "crop_yt" in state
    assert "crop_reels" in state
    assert "crop" not in state  # 舊欄位不再透出


def test_episode_io_save_writes_both_crops(tmp_episode_dir):
    from podcast_toolkit.web import episode_io
    from podcast_toolkit.episode import Episode
    import yaml
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(ep, {
        "crop_yt": {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0},
        "crop_reels": {"x": 0.3, "y": 0.0, "width": 0.4, "height": 1.0},
        "deletions": [],
        "cards": [],
    })
    data = yaml.safe_load((tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert data["crop_yt"]["width"] == 0.8
    assert data["crop_reels"]["width"] == 0.4
    assert "crop" not in data  # 舊欄位被清掉
