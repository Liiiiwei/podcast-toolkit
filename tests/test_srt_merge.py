"""srt_merge：N 路 mic SRT → 統一 timeline + speakers sidecar。

驗證重點：
  - 多路 SRT 依 start 時間排序 + 重編 idx
  - speakers sidecar = {new_idx: speaker_key}（同 cameras.json 形狀）
  - 同時講話（overlap）→ 兩個 cue 都保留，由 UI 端負責雙行渲染
  - run(ep) 串接 episode 路徑：讀 04_工作檔/_mic_*.srt → 寫 03_成品/_final_v2.{srt,speakers.json}
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcast_toolkit import srt_merge
from podcast_toolkit.episode import Episode


SRT_A = """\
1
00:00:00,000 --> 00:00:02,500
講者 a 第一句

2
00:00:05,000 --> 00:00:07,000
講者 a 第二句
"""

SRT_B = """\
1
00:00:02,800 --> 00:00:04,500
講者 b 接話

2
00:00:08,000 --> 00:00:10,000
講者 b 結尾
"""

# overlap：B 在 A 還沒結束時就開口（A: 0-3, B: 1.5-4.5）
SRT_A_OVERLAP = """\
1
00:00:00,000 --> 00:00:03,000
A 在講話被打斷
"""

SRT_B_OVERLAP = """\
1
00:00:01,500 --> 00:00:04,500
B 同時插話
"""


# --- merge_per_mic_srts：純函式 ---


def test_merge_orders_cues_by_start_time(tmp_path: Path):
    """A 兩句 + B 兩句 → 合併後依 start 時間排序，idx 重編 1..4。"""
    a = tmp_path / "a.srt"
    b = tmp_path / "b.srt"
    a.write_text(SRT_A, encoding="utf-8")
    b.write_text(SRT_B, encoding="utf-8")

    text, speakers = srt_merge.merge_per_mic_srts({"a": a, "b": b})

    from podcast_toolkit import srt_io
    cards = srt_io.parse(text)
    assert [c["idx"] for c in cards] == [1, 2, 3, 4]
    assert [c["start"] for c in cards] == [0.0, 2.8, 5.0, 8.0]
    assert speakers == {1: "a", 2: "b", 3: "a", 4: "b"}


def test_merge_preserves_card_text(tmp_path: Path):
    """合併後每張卡的內容不應被改動（mic 講什麼就什麼）。"""
    a = tmp_path / "a.srt"
    b = tmp_path / "b.srt"
    a.write_text(SRT_A, encoding="utf-8")
    b.write_text(SRT_B, encoding="utf-8")

    text, _ = srt_merge.merge_per_mic_srts({"a": a, "b": b})

    from podcast_toolkit import srt_io
    cards = srt_io.parse(text)
    texts = [c["text"] for c in cards]
    assert "講者 a 第一句" in texts
    assert "講者 b 接話" in texts
    assert "講者 a 第二句" in texts
    assert "講者 b 結尾" in texts


def test_merge_keeps_overlapping_cues_separately(tmp_path: Path):
    """同時講話：兩個 cue 都保留（不合併），UI 端再決定上下兩行渲染。"""
    a = tmp_path / "a.srt"
    b = tmp_path / "b.srt"
    a.write_text(SRT_A_OVERLAP, encoding="utf-8")
    b.write_text(SRT_B_OVERLAP, encoding="utf-8")

    text, speakers = srt_merge.merge_per_mic_srts({"a": a, "b": b})

    from podcast_toolkit import srt_io
    cards = srt_io.parse(text)
    assert len(cards) == 2
    assert speakers == {1: "a", 2: "b"}
    # 兩個 cue 時間區間明顯交疊
    assert cards[0]["end"] > cards[1]["start"]


def test_merge_single_mic(tmp_path: Path):
    """只有一路 mic → 結果 idx 連續，speakers 全部指向該 mic。"""
    a = tmp_path / "a.srt"
    a.write_text(SRT_A, encoding="utf-8")

    text, speakers = srt_merge.merge_per_mic_srts({"a": a})

    from podcast_toolkit import srt_io
    cards = srt_io.parse(text)
    assert len(cards) == 2
    assert speakers == {1: "a", 2: "a"}


def test_merge_three_mics_interleaved(tmp_path: Path):
    """三路 mic（a / b / c）交錯時間 → 合併後 idx 跨 mic 連續。"""
    a = tmp_path / "a.srt"
    b = tmp_path / "b.srt"
    c = tmp_path / "c.srt"
    a.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nA1\n", encoding="utf-8",
    )
    b.write_text(
        "1\n00:00:01,500 --> 00:00:02,500\nB1\n", encoding="utf-8",
    )
    c.write_text(
        "1\n00:00:03,000 --> 00:00:04,000\nC1\n", encoding="utf-8",
    )

    _, speakers = srt_merge.merge_per_mic_srts({"a": a, "b": b, "c": c})

    assert speakers == {1: "a", 2: "b", 3: "c"}


def test_merge_stable_tiebreak_on_same_start(tmp_path: Path):
    """兩 cue start 完全相同 → 依 speaker key 排序，可預期、可重現。"""
    a = tmp_path / "a.srt"
    b = tmp_path / "b.srt"
    a.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nA 同時\n", encoding="utf-8",
    )
    b.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nB 同時\n", encoding="utf-8",
    )

    _, speakers = srt_merge.merge_per_mic_srts({"a": a, "b": b})

    # a 排在 b 前（speaker key 排序）
    assert speakers == {1: "a", 2: "b"}


def test_merge_raises_when_srt_empty(tmp_path: Path):
    """SRT 檔存在但解析 0 張卡 → 明確 raise，下游不該收到空合併結果。"""
    a = tmp_path / "a.srt"
    a.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="空"):
        srt_merge.merge_per_mic_srts({"a": a})


def test_merge_raises_when_srt_missing(tmp_path: Path):
    """指定的 SRT 路徑不存在 → 明確 raise，不要靜默跳過。"""
    a = tmp_path / "a.srt"  # 沒寫入

    with pytest.raises(FileNotFoundError):
        srt_merge.merge_per_mic_srts({"a": a})


def test_merge_raises_when_input_empty():
    """傳空 dict → raise；表示 caller 把 episode mic 設定漏掉了。"""
    with pytest.raises(ValueError, match="至少一路"):
        srt_merge.merge_per_mic_srts({})


# --- run(ep)：episode 路徑串接 ---


def _setup_per_mic_srts(tmp_episode_dir: Path, mic_keys=("a", "b")):
    """在 04_工作檔/ 放 per-mic SRT + episode.yaml 補 mics 設定（讓 mic_paths 認得）。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    mics_block = "mics:\n"
    for k in mic_keys:
        mics_block += f"  {k}: 01_母帶/{{name}}_mic{k.upper()}.wav\n"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8") + mics_block,
        encoding="utf-8",
    )
    # 不需要真的放 mic.wav，run() 只讀 04_工作檔/_mic_*.srt
    if "a" in mic_keys:
        (tmp_episode_dir / "04_工作檔" / "測試集_mic_a.srt").write_text(
            SRT_A, encoding="utf-8",
        )
    if "b" in mic_keys:
        (tmp_episode_dir / "04_工作檔" / "測試集_mic_b.srt").write_text(
            SRT_B, encoding="utf-8",
        )


def test_run_writes_final_v2_srt_and_speakers_json(tmp_episode_dir: Path):
    """run(ep) → 03_成品/{name}_final_v2.srt + .speakers.json 兩個都要寫出。"""
    # tmp_episode_dir 預設有 03_成品/測試集_final_v2.srt sample，先清掉避免 skip
    (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").unlink()
    _setup_per_mic_srts(tmp_episode_dir)
    ep = Episode(tmp_episode_dir)

    rc = srt_merge.run(ep)

    assert rc == 0
    out_srt = tmp_episode_dir / "03_成品" / "測試集_final_v2.srt"
    out_json = tmp_episode_dir / "03_成品" / "測試集_final_v2.speakers.json"
    assert out_srt.is_file()
    assert out_json.is_file()

    speakers = json.loads(out_json.read_text(encoding="utf-8"))
    assert speakers == {"1": "a", "2": "b", "3": "a", "4": "b"}


def test_run_raises_when_no_mics_set(tmp_episode_dir: Path):
    """沒設 mics → 拒絕跑（避免下游拿到亂的合併結果）。"""
    ep = Episode(tmp_episode_dir)
    rc = srt_merge.run(ep)
    assert rc != 0


def test_run_raises_when_mic_srt_missing(tmp_episode_dir: Path):
    """mics 有 a + b 但 04_工作檔/ 只有 a 的 SRT → 明確錯（提示 user 跑 subtitle --per-mic）。"""
    (tmp_episode_dir / "03_成品" / "測試集_final_v2.srt").unlink()
    _setup_per_mic_srts(tmp_episode_dir, mic_keys=("a", "b"))
    # 砍掉 b 的 SRT
    (tmp_episode_dir / "04_工作檔" / "測試集_mic_b.srt").unlink()
    ep = Episode(tmp_episode_dir)

    rc = srt_merge.run(ep)
    assert rc != 0


def test_run_skips_when_output_exists_without_force(tmp_episode_dir: Path):
    """已存在的 _final_v2.srt 不覆寫（保護使用者手動編輯過的版本）。"""
    _setup_per_mic_srts(tmp_episode_dir)
    out_srt = tmp_episode_dir / "03_成品" / "測試集_final_v2.srt"
    out_srt.write_text("使用者改過的版本", encoding="utf-8")
    ep = Episode(tmp_episode_dir)

    rc = srt_merge.run(ep, force=False)

    assert rc != 0
    assert out_srt.read_text(encoding="utf-8") == "使用者改過的版本"


def test_run_force_overwrites_existing(tmp_episode_dir: Path):
    """--force → 覆寫舊的 _final_v2.srt 與 .speakers.json。"""
    _setup_per_mic_srts(tmp_episode_dir)
    out_srt = tmp_episode_dir / "03_成品" / "測試集_final_v2.srt"
    out_srt.write_text("舊版本", encoding="utf-8")
    ep = Episode(tmp_episode_dir)

    rc = srt_merge.run(ep, force=True)

    assert rc == 0
    assert "舊版本" not in out_srt.read_text(encoding="utf-8")
    assert "講者 a 第一句" in out_srt.read_text(encoding="utf-8")
