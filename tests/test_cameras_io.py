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


# ── speaker → cam 自動建議（home + feature 連續講夠久才切）─────────────────

def test_suggest_camera_cuts_feature_long_turn():
    """feature 講者(c→b)連續講滿 min_sec 才切到他的鏡頭；前面 home 段不切。"""
    cards = _cards((1, 0.0, 3.0), (2, 3.0, 25.0))  # a 講 3s（home）→ c 連講 22s
    trans = cameras_io.suggest_camera_cuts(
        {1: "a", 2: "c"}, cards, home_cam="a", feature_cam={"c": "b"}, min_sec=15
    )
    assert trans == [{"t": 3.0, "cam": "b"}]


def test_suggest_camera_cuts_short_turn_stays_home():
    """feature 講者只講一小段(<min_sec) → 留在 home，不切。"""
    cards = _cards((1, 0.0, 3.0), (2, 3.0, 8.0))  # c 只講 5s < 15
    trans = cameras_io.suggest_camera_cuts(
        {1: "a", 2: "c"}, cards, feature_cam={"c": "b"}, min_sec=15
    )
    assert trans == []


def test_suggest_camera_cuts_non_feature_speaker_stays_home():
    """不在 feature 的講者(主持 b)講再久也留 home。"""
    cards = _cards((1, 0.0, 40.0))  # b 講 40s 但不在 feature
    trans = cameras_io.suggest_camera_cuts(
        {1: "b"}, cards, feature_cam={"c": "b"}, min_sec=15
    )
    assert trans == []


def test_suggest_camera_cuts_back_to_home_after_turn():
    """切到 feature 後，講者換回別人 → 切回 home。"""
    cards = _cards((1, 0.0, 20.0), (2, 20.0, 40.0))  # c 連講 20s → b；主持接手 → 回 home
    trans = cameras_io.suggest_camera_cuts(
        {1: "c", 2: "a"}, cards, feature_cam={"c": "b"}, min_sec=15
    )
    assert trans == [{"t": 0.0, "cam": "b"}, {"t": 20.0, "cam": "a"}]


def test_suggest_camera_cuts_empty():
    assert cameras_io.suggest_camera_cuts({}, _cards((1, 0.0, 4.0))) == []


def test_suggest_cameras_run_roundtrip(tmp_episode_dir):
    """run()：讀 speakers.json + _v2 卡 → 套 camera_rule → 寫 cameras.json v2；--force 前備份。"""
    from podcast_toolkit.episode import Episode
    from podcast_toolkit import cameras_suggest, srt_io
    ep = Episode(tmp_episode_dir)
    # a 開場 3s（home）→ 來賓 c 連講 22s（≥ 預設 min_sec 15）→ 該切 cam b
    cards = [
        {"idx": 1, "start": 0.0, "end": 3.0, "text": "x"},
        {"idx": 2, "start": 3.0, "end": 25.0, "text": "y"},
    ]
    v2 = ep.output_v2_srt()
    v2.parent.mkdir(parents=True, exist_ok=True)
    v2.write_text(srt_io.serialize(cards), encoding="utf-8")
    cameras_io.save(ep.output_v2_speakers_json(), {1: "a", 2: "c"})

    assert cameras_suggest.run(ep) == 0
    assert cameras_io.load_transitions(ep.output_v2_cameras_json(), cards) == [{"t": 3.0, "cam": "b"}]
    # 已存在不覆蓋
    assert cameras_suggest.run(ep) == 1
    # --force：備份 .bak + 覆寫
    assert cameras_suggest.run(ep, force=True) == 0
    cam = ep.output_v2_cameras_json()
    assert cam.with_suffix(cam.suffix + ".bak").exists()


def test_suggest_cameras_run_missing_speakers(tmp_episode_dir):
    """沒 speakers.json（非分軌集）→ 回非零、不寫檔。"""
    from podcast_toolkit.episode import Episode
    from podcast_toolkit import cameras_suggest
    ep = Episode(tmp_episode_dir)
    assert cameras_suggest.run(ep) == 4
    assert not ep.output_v2_cameras_json().exists()
