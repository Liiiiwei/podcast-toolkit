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
