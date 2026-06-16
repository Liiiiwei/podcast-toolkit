"""web/episode_io：把 episode 資料夾轉成前端要的 JSON 狀態。"""
import pytest
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


def test_save_state_reorder_keeps_srt_time_monotonic(tmp_episode_dir):
    """回歸：拖拉換位置（time_overrides 把卡移過鄰居）存檔後，_v2.srt 必須仍時間單調、
    重新編號跟畫面（前端也依 start 排）一致。少了 always-sort 會寫出非單調 SRT →
    重載後「時間整個跑掉」。"""
    from podcast_toolkit import srt_io
    ep = Episode(tmp_episode_dir)
    # 卡 4（我們從牠的飼料配方開始講起，原 14–22s）拖到最前面 1.0–3.0s
    episode_io.save_state(ep, payload={
        "cards": [],
        "time_overrides": {"4": {"start": 1.0, "end": 3.0}},
    })
    out = srt_io.parse(
        (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").read_text(encoding="utf-8")
    )
    starts = [c["start"] for c in out]
    assert starts == sorted(starts), f"SRT 非單調（時間跑掉）：{starts}"
    assert len(out) == 4
    # 被拖到前面的卡 → 現在排第 2（緊接卡 1），時間 1.0–3.0
    assert out[1]["text"] == "我們從牠的飼料配方開始講起"
    assert out[1]["start"] == 1.0 and out[1]["end"] == 3.0
    # 重新編號連續 1..4
    assert [c["idx"] for c in out] == [1, 2, 3, 4]


def test_save_state_writes_rotate_cover_speed(tmp_episode_dir):
    """旋轉（per cam）/ 節目封面開關 / 倍速 存進 episode.yaml，load_state 再讀回。"""
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "rotate": {"a": 2.5, "b": -1.0},
            "cover_enabled": True,
            "speed": {"enabled": True, "factor": 1.25},
            "cards": [],
        },
    )
    data = yaml.safe_load((tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert data["rotate"] == {"a": 2.5, "b": -1.0}
    assert data["watermark"] == {"enabled": True}
    assert data["speed"] == {"enabled": True, "factor": 1.25}
    # load_state 透出給前端
    state = episode_io.load_state(Episode(tmp_episode_dir))
    assert state["rotate"] == {"a": 2.5, "b": -1.0}
    assert state["cover_enabled"] is True
    assert state["speed"]["enabled"] is True


def test_save_state_clears_rotate_and_speed_when_zero_or_off(tmp_episode_dir):
    """旋轉全 0 / 倍速關閉 / 封面取消 → 對應 key 從 yaml 移除（回退預設）。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    data["rotate"] = {"a": 3.0}
    data["speed"] = {"enabled": True, "factor": 1.5}
    data["watermark"] = {"enabled": True}
    yaml_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    episode_io.save_state(
        Episode(tmp_episode_dir),
        payload={
            "rotate": {"a": 0, "b": 0},
            "cover_enabled": False,
            "speed": {"enabled": False},
            "cards": [],
        },
    )
    after = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert "rotate" not in after
    assert "speed" not in after
    # 封面預設已開，取消要寫 explicit false（不能 pop，否則回退成預設 true）
    assert after["watermark"] == {"enabled": False}


def test_save_state_applies_time_overrides_to_v2_srt(tmp_episode_dir):
    """time_overrides 覆寫對應卡的 start/end，寫回 _v2.srt。"""
    from podcast_toolkit import srt_io
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "time_overrides": {"2": {"start": 5.0, "end": 10.5}},
            "cards": [],
        },
    )
    v2 = (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").read_text(encoding="utf-8")
    cards = srt_io.parse(v2)
    # 卡 2 的文字維持原樣、時間被覆寫
    c2 = next(c for c in cards if "過嗨乳牛這個議題" in c["text"])
    assert c2["start"] == pytest.approx(5.0)
    assert c2["end"] == pytest.approx(10.5)
    # 其他卡不受影響（卡 1 仍 0–4.2）
    c1 = cards[0]
    assert c1["start"] == pytest.approx(0.0)
    assert c1["end"] == pytest.approx(4.2)


def test_save_state_ignores_invalid_time_override(tmp_episode_dir):
    """end <= start 的非法時間值要被丟掉，不寫回。"""
    from podcast_toolkit import srt_io
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={"time_overrides": {"1": {"start": 8.0, "end": 3.0}}, "cards": []},
    )
    v2 = (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").read_text(encoding="utf-8")
    c1 = srt_io.parse(v2)[0]
    assert c1["start"] == pytest.approx(0.0)  # 維持原值
    assert c1["end"] == pytest.approx(4.2)


def test_save_state_inserts_new_cards_in_time_order(tmp_episode_dir):
    """new_cards 被 append 進 SRT、依時間排序、重編號連續。"""
    from podcast_toolkit import srt_io
    ep = Episode(tmp_episode_dir)
    # 在卡 1(0–4.2) 與卡 2(4.2–12) 之間插一張新卡
    episode_io.save_state(
        ep,
        payload={
            "new_cards": [{"start": 4.0, "end": 4.2, "text": "插入的新句"}],
            "cards": [],
        },
    )
    v2 = (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").read_text(encoding="utf-8")
    cards = srt_io.parse(v2)
    assert len(cards) == 5  # 原 4 張 + 1 新
    # idx 連續 1..5
    assert [c["idx"] for c in cards] == [1, 2, 3, 4, 5]
    # 依時間排序：新卡(4.0) 落在原卡 1(0) 之後、原卡 2(4.2) 之前
    new_pos = next(i for i, c in enumerate(cards) if c["text"] == "插入的新句")
    assert cards[new_pos - 1]["text"] == "大家好歡迎來到我愛上班"
    assert cards[new_pos]["start"] == pytest.approx(4.0)
    assert cards[new_pos]["end"] == pytest.approx(4.2)


def test_save_state_skips_invalid_new_cards(tmp_episode_dir):
    """end <= start 的新卡要被丟掉。"""
    from podcast_toolkit import srt_io
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={"new_cards": [{"start": 5.0, "end": 5.0, "text": "壞卡"}], "cards": []},
    )
    v2 = (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").read_text(encoding="utf-8")
    cards = srt_io.parse(v2)
    assert len(cards) == 4  # 沒新增
    assert all("壞卡" not in c["text"] for c in cards)


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
    """save_state 把 cameras_mapping 轉成**時間版切換點**寫進 sidecar JSON。"""
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
    # 卡1標a(預設就是a→冗餘不產生切換)、卡3 start=12.0 切 b
    assert data["version"] == 2
    assert data["transitions"] == [{"t": 12.0, "cam": "b"}]


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
    # 'c'(卡2 @4.2s)與 None(卡4)被過濾掉 → 只剩卡3 的 b @12.0
    assert data["transitions"] == [{"t": 12.0, "cam": "b"}]


# --- 分軌 speaker mapping：UI 端要拿到 mics + speakers_mapping ---


def _add_mics_to_yaml(tmp_episode_dir, mic_keys=("a", "b")):
    """工具：在 episode.yaml 補一段 mics 設定，模擬分軌集情境。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    block = "mics:\n"
    for k in mic_keys:
        block += f"  {k}: 01_母帶/{{name}}_mic{k.upper()}.wav\n"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8") + block,
        encoding="utf-8",
    )


def test_load_state_returns_empty_mics_when_not_set(tmp_episode_dir):
    """沒設 mics → state.mics 是空 dict（前端據此判斷是不是分軌集）。"""
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["mics"] == {}


def test_load_state_returns_mics_dict_when_set(tmp_episode_dir):
    """yaml.mics → state.mics 帶原始相對路徑（前端只看 keys 就夠了，路徑備用）。"""
    _add_mics_to_yaml(tmp_episode_dir, ("a", "b"))
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert set(state["mics"].keys()) == {"a", "b"}


def test_load_state_returns_empty_speakers_mapping_when_no_sidecar(tmp_episode_dir):
    """sidecar 不存在 → speakers_mapping 是空 dict。"""
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["speakers_mapping"] == {}


def test_load_state_returns_speakers_mapping_from_sidecar(tmp_episode_dir):
    """sidecar 存在 → speakers_mapping 從 JSON 讀出來（int key、shape 同 cameras）。"""
    sidecar = tmp_episode_dir / "03_成品" / "測試集_final_v2.speakers.json"
    sidecar.write_text('{"1": "a", "2": "b", "3": "a"}', encoding="utf-8")
    ep = Episode(tmp_episode_dir)
    state = episode_io.load_state(ep)
    assert state["speakers_mapping"] == {1: "a", 2: "b", 3: "a"}


def test_save_state_writes_speakers_mapping_sidecar(tmp_episode_dir):
    """前端改完 speaker tag → speakers_mapping 寫進 sidecar JSON。"""
    _add_mics_to_yaml(tmp_episode_dir, ("a", "b"))
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None,
            "crop_reels": None,
            "deletions": [],
            "cards": [],
            "speakers_mapping": {1: "a", 2: "b"},
        },
    )
    sidecar = tmp_episode_dir / "03_成品" / "測試集_final_v2.speakers.json"
    assert sidecar.exists()
    import json
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data == {"1": "a", "2": "b"}


def test_save_state_empty_speakers_mapping_removes_sidecar(tmp_episode_dir):
    """空 mapping → 舊 sidecar 應被刪掉（避免殘留亂值）。"""
    _add_mics_to_yaml(tmp_episode_dir, ("a", "b"))
    sidecar = tmp_episode_dir / "03_成品" / "測試集_final_v2.speakers.json"
    sidecar.write_text('{"1": "a"}', encoding="utf-8")
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "speakers_mapping": {},
        },
    )
    assert not sidecar.exists()


def test_save_state_filters_speakers_not_in_mics(tmp_episode_dir):
    """mics 只有 a / b → 前端送來 c 或 None 要被濾掉，sidecar 不能寫入垃圾值。"""
    _add_mics_to_yaml(tmp_episode_dir, ("a", "b"))
    ep = Episode(tmp_episode_dir)
    episode_io.save_state(
        ep,
        payload={
            "crop_yt": None, "crop_reels": None, "deletions": [], "cards": [],
            "speakers_mapping": {1: "a", 2: "c", 3: "b", 4: None},
        },
    )
    sidecar = tmp_episode_dir / "03_成品" / "測試集_final_v2.speakers.json"
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
    from podcast_toolkit import srt_io
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    # 新 idx：2:0→2(a 冗餘)、2:1→3(b)、4→5(b carry 冗餘) → 只有一個切到 b，時間=新卡3 start
    v2cards = srt_io.parse(
        (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").read_text(encoding="utf-8")
    )
    card3_start = next(c["start"] for c in v2cards if c["idx"] == 3)
    assert data["version"] == 2
    assert data["transitions"] == [{"t": round(card3_start, 3), "cam": "b"}]


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


# --- T31a：分軌轉錄 mics / speakers sidecar ---


def test_mic_paths_empty_when_mics_not_set(tmp_episode_dir):
    """沒設 mics → 空 dict，呼叫端 fallback 走混音軌路線。"""
    ep = Episode(tmp_episode_dir)
    assert ep.mic_paths() == {}


def test_mic_paths_resolves_per_speaker_to_absolute(tmp_episode_dir):
    """mics 設了三路 → speaker key → 絕對路徑，{name} placeholder 自動展開。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "mics:\n"
        + "  a: 01_母帶/{name}_micA.wav\n"
        + "  b: 01_母帶/{name}_micB.wav\n"
        + "  c: 01_母帶/{name}_micC.wav\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    paths = ep.mic_paths()
    assert set(paths.keys()) == {"a", "b", "c"}
    assert paths["a"] == tmp_episode_dir / "01_母帶" / "測試集_micA.wav"
    assert paths["b"] == tmp_episode_dir / "01_母帶" / "測試集_micB.wav"
    assert paths["c"] == tmp_episode_dir / "01_母帶" / "測試集_micC.wav"


def test_mic_paths_skips_empty_values(tmp_episode_dir):
    """mics 裡空字串 / None 不該透出（避免下游拿到無效路徑）。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "mics:\n  a: 01_母帶/{name}_micA.wav\n  b: ''\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    paths = ep.mic_paths()
    assert list(paths.keys()) == ["a"]


def test_output_v2_speakers_json_path(tmp_episode_dir):
    """speakers sidecar 命名與 cameras.json 同形狀（_final_v2.speakers.json）。"""
    ep = Episode(tmp_episode_dir)
    out = ep.output_v2_speakers_json()
    assert out.name == "測試集_final_v2.speakers.json"
    assert out.parent.name == "03_成品"


def test_per_mic_gated_wav_path(tmp_episode_dir):
    """gated wav 落在 04_工作檔/，命名帶 speaker key 區分多路。"""
    ep = Episode(tmp_episode_dir)
    out = ep.per_mic_gated_wav("a")
    assert out.name == "測試集_micgate_a.wav"
    assert out.parent.name == "04_工作檔"


def test_per_mic_srt_path(tmp_episode_dir):
    """單路 mic 轉錄 SRT 落在 04_工作檔/，待 srt_merge 合併。"""
    ep = Episode(tmp_episode_dir)
    out = ep.per_mic_srt("b")
    assert out.name == "測試集_mic_b.srt"
    assert out.parent.name == "04_工作檔"
