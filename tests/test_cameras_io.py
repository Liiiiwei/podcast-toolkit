"""字幕卡 → 鏡頭 sidecar 的讀寫行為。"""
from pathlib import Path

from podcast_toolkit import cameras_io


def test_load_missing_file_returns_empty(tmp_path: Path):
    """沒檔案 → 空 dict，不 raise。"""
    assert cameras_io.load(tmp_path / "nope.json") == {}


def test_save_then_load_roundtrips_int_keys(tmp_path: Path):
    """key 進去是 int，出來也是 int（JSON 中間會變字串但要還原）。"""
    path = tmp_path / "cams.json"
    cameras_io.save(path, {1: "a", 5: "b", 12: "a"})
    assert cameras_io.load(path) == {1: "a", 5: "b", 12: "a"}


def test_save_empty_mapping_deletes_existing_file(tmp_path: Path):
    """空 mapping 把舊檔刪掉，避免噪音。"""
    path = tmp_path / "cams.json"
    cameras_io.save(path, {3: "b"})
    assert path.exists()
    cameras_io.save(path, {})
    assert not path.exists()


def test_save_empty_mapping_no_file_no_op(tmp_path: Path):
    """檔案沒存在、mapping 也空 → 不該 raise。"""
    path = tmp_path / "cams.json"
    cameras_io.save(path, {})
    assert not path.exists()


def test_save_produces_human_readable_json(tmp_path: Path):
    """sorted + indented，方便手動 review/diff。"""
    path = tmp_path / "cams.json"
    cameras_io.save(path, {5: "b", 1: "a"})
    text = path.read_text(encoding="utf-8")
    # sort_keys=True 確保 "1" 排在 "5" 前
    assert text.index('"1"') < text.index('"5"')
    assert "\n" in text  # indent


def test_episode_output_v2_cameras_json_path(tmp_episode_dir):
    """sidecar 路徑要和 _v2.srt 同目錄、同前綴。"""
    from podcast_toolkit.episode import Episode
    ep = Episode(tmp_episode_dir)
    p = ep.output_v2_cameras_json()
    assert p.name == "測試集_final_v2.cameras.json"
    assert p.parent.name == "03_成品"


# ── 時間版鏡頭 transition ──────────────────────────────────────────────

def _cards(*rows):
    return [{"idx": i, "start": s, "end": e, "text": ""} for i, s, e in rows]


def test_card_mapping_to_transitions_carry_forward():
    """idx→cam（含冗餘同 cam 標記）→ 只留實際切換點。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0))
    # 卡1標a(冗餘，預設就是a)、卡2標b、卡3標b(冗餘) → 只該有一個 4.0→b
    trans = cameras_io.card_mapping_to_transitions({1: "a", 2: "b", 3: "b"}, cards)
    assert trans == [{"t": 4.0, "cam": "b"}]


def test_transitions_to_card_mapping_snaps_to_nearest_card():
    """切換點時間吸附到 start 最近的卡。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0))
    # 5.0 最近卡2(start 4.0)；7.9 也最近卡2 → 後者覆蓋，仍標卡2
    m = cameras_io.transitions_to_card_mapping([{"t": 4.2, "cam": "b"}], cards)
    assert m == {2: "b"}


def test_load_transitions_new_format(tmp_path):
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 8.0))
    path = tmp_path / "cam.json"
    cameras_io.save_transitions(path, [{"t": 4.0, "cam": "b"}])
    assert cameras_io.load_transitions(path, cards) == [{"t": 4.0, "cam": "b"}]


def test_load_transitions_migrates_legacy_idx_format(tmp_path):
    """讀到舊 {idx:cam} 自動用 cards 換算成時間版。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0))
    path = tmp_path / "cam.json"
    cameras_io.save(path, {2: "b"})  # 舊 flat 格式
    assert cameras_io.load_transitions(path, cards) == [{"t": 4.0, "cam": "b"}]


def test_save_transitions_writes_v2_format(tmp_path):
    path = tmp_path / "cam.json"
    cameras_io.save_transitions(path, [{"t": 4.0, "cam": "b"}])
    import json
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 2
    assert raw["transitions"] == [{"t": 4.0, "cam": "b"}]


def test_save_empty_transitions_deletes_file(tmp_path):
    path = tmp_path / "cam.json"
    cameras_io.save_transitions(path, [{"t": 1.0, "cam": "b"}])
    assert path.exists()
    cameras_io.save_transitions(path, [])
    assert not path.exists()


def test_legacy_load_ignores_new_format(tmp_path):
    """flat load() 碰到新時間版格式回空（不誤把它當 speakers）。"""
    path = tmp_path / "cam.json"
    cameras_io.save_transitions(path, [{"t": 1.0, "cam": "b"}])
    assert cameras_io.load(path) == {}
