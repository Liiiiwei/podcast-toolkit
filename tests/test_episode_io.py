"""web/episode_io：把 episode 資料夾轉成前端要的 JSON 狀態。"""
from podcast_toolkit.episode import Episode
from podcast_toolkit.web import episode_io


def test_load_state_returns_name_and_cards(tmp_episode_dir):
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["name"] == "測試集"
    assert state["crop"] is None
    assert state["deletions"] == []
    assert len(state["cards"]) == 4
    assert state["cards"][0]["idx"] == 1
    assert state["cards"][0]["text"] == "大家好歡迎來到我愛上班"


def test_load_state_includes_crop_and_deletions_from_yaml(tmp_episode_dir):
    # 改寫 yaml 加 crop / deletions
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "crop:\n  x: 0.1\n  y: 0.0\n  width: 0.8\n  height: 1.0\n"
        + "deletions: [3]\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["crop"] == {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}
    assert state["deletions"] == [3]
