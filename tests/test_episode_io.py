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


def test_load_state_returns_head_tail_trim_from_yaml(tmp_episode_dir):
    """T21: episode.yaml 有 head_trim_sec / tail_trim_sec 時，load_state 要透出來。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "head_trim_sec: 1.5\ntail_trim_sec: 2.0\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["head_trim_sec"] == 1.5
    assert state["tail_trim_sec"] == 2.0


def test_load_state_defaults_head_tail_trim_to_zero(tmp_episode_dir):
    """T21: 沒設過時 head_trim_sec / tail_trim_sec 預設 0。"""
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["head_trim_sec"] == 0.0
    assert state["tail_trim_sec"] == 0.0


def test_save_state_writes_head_tail_trim(tmp_episode_dir):
    """T21: save_state 把 head_trim_sec / tail_trim_sec 寫進 yaml。"""
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None,
            "crop_reels": None,
            "deletions": [],
            "cards": [],
            "head_trim_sec": 1.5,
            "tail_trim_sec": 2.0,
        },
    )
    data = yaml.safe_load((tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert data["head_trim_sec"] == 1.5
    assert data["tail_trim_sec"] == 2.0


def test_save_state_removes_head_tail_trim_when_zero(tmp_episode_dir):
    """T21: trim = 0 時把 key 從 yaml 移除，避免噪音。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "head_trim_sec: 1.5\ntail_trim_sec: 2.0\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "head_trim_sec": 0, "tail_trim_sec": 0,
        },
    )
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert "head_trim_sec" not in data
    assert "tail_trim_sec" not in data


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


# --- T23a：雙鏡頭資訊透出 / sidecar 讀寫 ---


def test_load_state_exposes_cameras_from_cfg(tmp_episode_dir):
    """單機集 cameras 只有 a（從 main_video 遷移）。"""
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["cameras"] == {"a": "01_母帶/{name}.mp4"}
    assert state["camera_sync_offset"] == {}
    assert state["audio"] is None


def test_load_state_exposes_cameras_dict_when_dual_cam(tmp_episode_dir):
    """有 cameras dict 時要透出兩個鏡頭。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "cameras:\n  a: 01_母帶/cam_a.mp4\n  b: 01_母帶/cam_b.mp4\n"
        + "audio:\n  main: 01_母帶/stereo.wav\n  sync_ref: a\n  offset_sec: 0.0\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["cameras"] == {"a": "01_母帶/cam_a.mp4", "b": "01_母帶/cam_b.mp4"}
    assert state["audio"] == {"main": "01_母帶/stereo.wav", "sync_ref": "a", "offset_sec": 0.0}


def test_load_state_returns_empty_cameras_mapping_when_no_sidecar(tmp_episode_dir):
    """sidecar 不存在 → cameras_mapping 是空 dict。"""
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["cameras_mapping"] == {}


def test_load_state_returns_cameras_mapping_from_sidecar(tmp_episode_dir):
    """sidecar 存在 → cameras_mapping 從 JSON 讀出來（int key）。"""
    sidecar = tmp_episode_dir / "03_成品" / "測試集_final_v2.cameras.json"
    sidecar.write_text('{"2": "b", "4": "a"}', encoding="utf-8")
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["cameras_mapping"] == {2: "b", 4: "a"}


def test_save_state_writes_cameras_mapping_sidecar(tmp_episode_dir):
    """save_state 把 cameras_mapping 寫進 sidecar JSON。"""
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None,
            "crop_reels": None,
            "deletions": [],
            "cards": [],
            "cameras_mapping": {1: "a", 3: "b"},
        },
    )
    sidecar = tmp_episode_dir / "03_成品" / "測試集_final_v2.cameras.json"
    assert sidecar.exists()
    import json
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data == {"1": "a", "3": "b"}


def test_save_state_empty_cameras_mapping_removes_sidecar(tmp_episode_dir):
    """空 mapping 把舊 sidecar 刪掉。"""
    sidecar = tmp_episode_dir / "03_成品" / "測試集_final_v2.cameras.json"
    sidecar.write_text('{"1": "b"}', encoding="utf-8")
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "cameras_mapping": {},
        },
    )
    assert not sidecar.exists()


def test_save_state_filters_invalid_camera_values(tmp_episode_dir):
    """payload 裡若混進 'c' 或 None 之類的雜值要過濾掉。"""
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "cameras_mapping": {1: "a", 2: "c", 3: "b", 4: None},
        },
    )
    sidecar = tmp_episode_dir / "03_成品" / "測試集_final_v2.cameras.json"
    import json
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data == {"1": "a", "3": "b"}


# --- T23a-followup：cam B 設定 UI（消除手改 yaml）---


def test_save_state_writes_cam_b_to_yaml(tmp_episode_dir):
    """payload 帶 cam_b_path → cameras.b 寫入 yaml；cameras.a 從原本 main_video 帶上來。"""
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "cam_b_path": "01_母帶/{name}_camB.mp4",
        },
    )
    data = yaml.safe_load((tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert data["cameras"]["a"] == "01_母帶/{name}.mp4"
    assert data["cameras"]["b"] == "01_母帶/{name}_camB.mp4"


def test_save_state_clears_cam_b_when_empty_string(tmp_episode_dir):
    """cam_b_path 是空字串 → 從 yaml 拿掉 cameras.b（明確清空）。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "cameras:\n  a: 01_母帶/{name}.mp4\n  b: 01_母帶/{name}_camB.mp4\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "cam_b_path": "",
        },
    )
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    cameras = data.get("cameras") or {}
    assert "b" not in cameras


def test_save_state_writes_camera_sync_offset_b(tmp_episode_dir):
    """payload 帶 camera_sync_offset_b > 0 → camera_sync_offset.b 寫入。"""
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "cam_b_path": "01_母帶/{name}_camB.mp4",
            "camera_sync_offset_b": 1.25,
        },
    )
    data = yaml.safe_load((tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert data["camera_sync_offset"]["b"] == 1.25


def test_save_state_removes_camera_sync_offset_when_zero(tmp_episode_dir):
    """offset = 0 → camera_sync_offset 整個拿掉。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "camera_sync_offset:\n  b: 1.25\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "camera_sync_offset_b": 0,
        },
    )
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert "camera_sync_offset" not in data


def test_save_state_no_cam_b_key_in_payload_preserves_yaml(tmp_episode_dir):
    """payload 沒帶 cam_b_path key → 不動原本 yaml 的 cameras（向後相容）。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "cameras:\n  a: 01_母帶/{name}.mp4\n  b: 01_母帶/{name}_camB.mp4\n"
        + "camera_sync_offset:\n  b: 1.25\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
        },
    )
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert data["cameras"]["b"] == "01_母帶/{name}_camB.mp4"
    assert data["camera_sync_offset"]["b"] == 1.25


def test_load_state_lists_cam_b_candidates(tmp_episode_dir):
    """01_母帶/ 下其他 mp4 列為 cam B 候選（排除 cam A）。"""
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"")
    (tmp_episode_dir / "01_母帶" / "測試集_camB.mp4").write_bytes(b"")
    (tmp_episode_dir / "01_母帶" / "B-roll.mp4").write_bytes(b"")
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    # cam A 不能在裡面
    assert "01_母帶/測試集.mp4" not in state["cam_b_candidates"]
    # 其他兩個要
    assert "01_母帶/測試集_camB.mp4" in state["cam_b_candidates"]
    assert "01_母帶/B-roll.mp4" in state["cam_b_candidates"]


def test_load_state_cam_b_candidates_empty_when_only_cam_a(tmp_episode_dir):
    """只有 cam A 那一個檔 → 候選清單為空。"""
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"")
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["cam_b_candidates"] == []


def test_load_state_cam_b_candidates_handles_uppercase_extension(tmp_episode_dir):
    """DJI 等相機常出大寫 .MP4；候選掃描要 case-insensitive。"""
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"")
    (tmp_episode_dir / "01_母帶" / "DJI_001.MP4").write_bytes(b"")
    (tmp_episode_dir / "01_母帶" / "DJI_002.Mp4").write_bytes(b"")
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert "01_母帶/DJI_001.MP4" in state["cam_b_candidates"]
    assert "01_母帶/DJI_002.Mp4" in state["cam_b_candidates"]


# --- Reels 片段：load / save round-trip ---


def test_load_state_returns_empty_reels_clips_default(tmp_episode_dir):
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["reels_clips"] == []


def test_load_state_returns_reels_clips_from_yaml(tmp_episode_dir):
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "reels_clips:\n  - {name: hook1, start_card: 1, end_card: 3}\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["reels_clips"] == [
        {"name": "hook1", "start_card": 1, "end_card": 3}
    ]


def test_save_state_writes_reels_clips(tmp_episode_dir):
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "reels_clips": [
                {"name": "hook1", "start_card": 1, "end_card": 3},
                {"name": "punch", "start_card": 5, "end_card": 8},
            ],
        },
    )
    data = yaml.safe_load((tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert data["reels_clips"] == [
        {"name": "hook1", "start_card": 1, "end_card": 3},
        {"name": "punch", "start_card": 5, "end_card": 8},
    ]


def test_save_state_removes_reels_clips_when_empty(tmp_episode_dir):
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "reels_clips:\n  - {name: hook1, start_card: 1, end_card: 3}\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "reels_clips": [],
        },
    )
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert "reels_clips" not in data


def test_save_state_filters_invalid_reels_clips(tmp_episode_dir):
    """缺欄位 / 型別錯誤的 clip 直接過濾掉，不寫進 yaml。"""
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "reels_clips": [
                {"name": "ok", "start_card": 1, "end_card": 3},
                {"name": "", "start_card": 2, "end_card": 4},          # 空 name
                {"name": "no_start", "end_card": 5},                    # 缺 start_card
                {"name": "bad_type", "start_card": "x", "end_card": 7}, # 型別錯
                "not_a_dict",
            ],
        },
    )
    data = yaml.safe_load((tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert data["reels_clips"] == [{"name": "ok", "start_card": 1, "end_card": 3}]


# --- 前端 Enter 切卡：splits payload + 翻譯 deletions / cameras_mapping / textOverrides ---


def test_save_state_applies_splits_to_v2_srt(tmp_episode_dir):
    """splits payload 切第 2 卡 → _v2.srt 從 4 段變 5 段、idx 重編。"""
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "splits": {"2": ["今天要聊的是", "過嗨乳牛這個議題"]},
        },
    )
    v2 = (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").read_text(encoding="utf-8")
    # 原 4 段 → 切後 5 段
    lines = [l for l in v2.strip().split("\n") if l.strip().isdigit()]
    assert lines == ["1", "2", "3", "4", "5"]
    assert "今天要聊的是" in v2
    assert "過嗨乳牛這個議題" in v2


def test_save_state_translates_deletions_via_composite_id(tmp_episode_dir):
    """切第 2 卡 + 刪「sub-card 1」 → 翻譯成新 idx 3 寫進 yaml.deletions。
    第 3 卡（原本後面）會被擠到新 idx 4，也要驗證後面卡的 deletion 跟著挪。"""
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "cards": [],
            "splits": {"2": ["前段", "後段"]},
            # 原 2 卡的第 1 半（part 1）→ 新 idx 3；原 3 卡（未切）→ 新 idx 4
            "deletions": ["2:1", 3],
        },
    )
    data = yaml.safe_load((tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert sorted(data["deletions"]) == [3, 4]


def test_save_state_translates_cameras_mapping_via_composite_id(tmp_episode_dir):
    """切第 2 卡 + cameras_mapping 標 "2:0"=a + "2:1"=b + 4=b → 寫進 sidecar 為新 idx 2/3/5。"""
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "splits": {"2": ["前", "後"]},
            "cameras_mapping": {"2:0": "a", "2:1": "b", 4: "b"},
        },
    )
    sidecar = tmp_episode_dir / "03_成品" / "測試集_final_v2.cameras.json"
    import json
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    # 新 idx：原 1 → 1；原 2:0 → 2；原 2:1 → 3；原 3 → 4；原 4 → 5
    assert data == {"2": "a", "3": "b", "5": "b"}


def test_save_state_splits_with_text_overrides_on_other_card(tmp_episode_dir):
    """splits[2] + cards[3] (text override on un-split card) 兩個都生效。"""
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [],
            "cards": [{"idx": 3, "text": "嗯"}],
            "splits": {"2": ["前段文字", "後段文字"]},
        },
    )
    v2 = (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").read_text(encoding="utf-8")
    assert "前段文字" in v2 and "後段文字" in v2
    # 原 3 卡（被切後變新 4）文字被改成「嗯」
    assert "\n4\n" in v2
    assert "嗯" in v2
    assert "呃那個" not in v2


def test_save_state_splits_drop_invalid_payload_keys(tmp_episode_dir):
    """splits 非 list / 1 段以下 / key 非 int → 忽略，不算切。"""
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "splits": {
                "2": ["只給一段"],     # 1 段不算切
                "x": ["a", "b"],       # key 非 int
                "3": "not a list",     # 不是 list
            },
        },
    )
    v2 = (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").read_text(encoding="utf-8")
    lines = [l for l in v2.strip().split("\n") if l.strip().isdigit()]
    assert lines == ["1", "2", "3", "4"]  # 4 段不變


def test_save_state_drops_deletion_pointing_to_missing_sub_card(tmp_episode_dir):
    """未切的卡來 composite id "2:1" 找不到對應 sub-card → 過濾掉（避免殘影）。"""
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "cards": [],
            "deletions": ["1:1", 2],  # "1:1" 沒切過 → 應跳過；2 正常翻譯
        },
    )
    data = yaml.safe_load((tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert data["deletions"] == [2]


def test_save_state_no_reels_clips_key_preserves_yaml(tmp_episode_dir):
    """payload 完全沒帶 reels_clips key → 不動原本 yaml。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "reels_clips:\n  - {name: existing, start_card: 2, end_card: 4}\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
        },
    )
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert data["reels_clips"] == [{"name": "existing", "start_card": 2, "end_card": 4}]
