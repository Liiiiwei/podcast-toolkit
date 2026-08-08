"""寫入端 → episode.yaml → config.merge 的 round-trip 守門（自動列舉版）。

背景：episode.yaml 的鍵分兩類——defaults.yaml 有的鍵走自動深合併（免白名單）；
episode-only 鍵（defaults 沒有）必須在 config.merge 明確透傳，漏列＝「寫得進讀不回」
（srt_path / subtitle_offset_sec 都是前科，見 config.py merge() 內註解）。

與 test_config_merge.py 的分工：那邊是「出過事的鍵」逐一補的行為測試；
這邊把「寫入端源碼」當單一事實來源——正則掃出所有 data["key"] 賦值，
逐鍵驗 merge 後讀得回。之後在寫入端新增鍵而沒接 merge（或沒補樣本值），
這支測試會直接紅，不用等 59 天後使用者回報「存了沒生效」。
"""
import re
from pathlib import Path

import pytest
import yaml

from podcast_toolkit import config

ROOT = Path(__file__).resolve().parent.parent

# 會把鍵寫進 episode.yaml 的源碼（新增寫入端檔案時要補進來）
_WRITER_SOURCES = [
    ROOT / "podcast_toolkit" / "web" / "episode_io.py",
    ROOT / "podcast_toolkit" / "autotrim.py",
]

# data["key"] = … 直接賦值；autotrim 走 changes["key"] 再 data.update(changes)
_ASSIGN_RE = re.compile(r'(?:data|changes)\["([a-z_]+)"\]\s*=')

# episode_io.py 的 crop 迴圈用變數寫入（for key in ("crop_yt", "crop_reels")），
# 正則掃不到，手動列。新增這種寫法時也要補這裡。
_LOOP_WRITTEN = {"crop_yt", "crop_reels"}


def _scan_written_keys() -> set:
    keys = set()
    for src in _WRITER_SOURCES:
        keys |= set(_ASSIGN_RE.findall(src.read_text(encoding="utf-8")))
    return keys | _LOOP_WRITTEN


# 每個可寫鍵的代表值（貼近寫入端實際形狀）。
# 新增可寫鍵時：在這裡補樣本值，並確認 config.merge 讀得回
# （episode-only 鍵要加透傳；defaults 有的 dict 鍵自動深合併）。
SAMPLES = {
    "mics": {"a": "01_母帶/{name}_micA.wav", "b": "01_母帶/{name}_micB.wav"},
    "camera_rule": {"home": "a", "feature": {"b2": "b"}, "min_sec": 15.0},
    "rotate": {"a": -1.2, "b": 0.6},
    "watermark": {"enabled": True},
    "speed": {"enabled": True, "factor": 1.5},
    "silence_trim": {"enabled": True},
    "cuts": [[12.0, 15.5], [80.25, 82.0]],
    "reels_clips": [{"name": "hook1", "start_card": 5, "end_card": 12}],
    "cameras": {"a": "01_母帶/cam_a.mp4", "b": "01_母帶/cam_b.mp4"},
    "main_video": "01_母帶/{name}.mp4",
    "camera_sync_offset": {"b": 0.42},
    "srt_path": "04_工作檔/chosen.srt",
    "subtitle_offset_sec": 0.35,
    "audio": {"main": "01_母帶/stereo.wav", "sync_ref": "a", "offset_sec": 0.0},
    "deletions": [2, 4, 7],
    "head_trim_sec": 1.25,
    "tail_trim_sec": 2.5,
    "crop_yt": {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0},
    "crop_reels": {"x": 0.3, "y": 0.0, "width": 0.4, "height": 1.0},
}

DEFAULTS_REAL = config.load_defaults()


def test_written_keys_and_samples_in_sync():
    """寫入端掃出的鍵集合必須與 SAMPLES 完全一致。
    紅在「多出」＝寫入端新增了鍵：補 SAMPLES 樣本值＋確認 merge 讀得回。
    紅在「缺少」＝寫入端移除了鍵：把 SAMPLES 的殘留條目刪掉。"""
    written = _scan_written_keys()
    assert written == set(SAMPLES), (
        f"寫入端新增未列樣本的鍵：{sorted(written - set(SAMPLES))}；"
        f"SAMPLES 殘留已不寫入的鍵：{sorted(set(SAMPLES) - written)}"
    )


def _assert_subset(sample: dict, actual: dict, path: str):
    """sample 的每個欄位都要出現在 actual（deep merge 會多出 defaults 欄位，屬正常）。"""
    for k, v in sample.items():
        assert k in actual, f"{path}.{k} 讀不回（寫入 {v!r}）"
        if isinstance(v, dict) and isinstance(actual.get(k), dict):
            _assert_subset(v, actual[k], f"{path}.{k}")
        else:
            assert actual[k] == v, f"{path}.{k}：寫入 {v!r}，讀回 {actual[k]!r}"


@pytest.mark.parametrize("key", sorted(SAMPLES))
def test_roundtrip_written_key_survives_merge(key):
    """寫入→（真實 YAML dump/load）→merge，值要讀得回。
    defaults 有的 dict 鍵走深合併 → 驗子集（defaults 欄位混進來正常）；
    其餘（episode-only 透傳）→ 驗全等。"""
    sample = SAMPLES[key]
    episode = yaml.safe_load(
        yaml.safe_dump({"name": "t", key: sample}, allow_unicode=True)
    )
    cfg = config.merge(DEFAULTS_REAL, episode)
    assert key in cfg, (
        f"episode.yaml 的 {key} 沒接進 config.merge——寫得進讀不回。"
        f"episode-only 鍵要在 merge() 加透傳；defaults 的 dict 鍵確認沒被列進 deny-list。"
    )
    if key in DEFAULTS_REAL and isinstance(sample, dict):
        _assert_subset(sample, cfg[key], key)
    else:
        assert cfg[key] == sample, f"{key}：寫入 {sample!r}，讀回 {cfg[key]!r}"


def test_unhandled_key_is_dropped_negative_control():
    """突變對照組：一個沒人接的 episode-only 鍵，merge 後必須驗得出「會掉」。
    這條若綠不了，代表 merge 行為變了（例如改成全鍵透傳），上面整套檢查邏輯要重審。"""
    cfg = config.merge(DEFAULTS_REAL, {"name": "t", "zz_unhandled_key": 123})
    assert "zz_unhandled_key" not in cfg
