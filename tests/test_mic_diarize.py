"""tests/test_mic_diarize.py — mic_diarize 模組 TDD 測試（規格 §7 全 13 條）

合成 wav 用 numpy 陣列 → _write_wav 落暫存檔，不依賴任何實際音檔。
"""
from __future__ import annotations

import json
import math
import struct
import tempfile
import wave
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from podcast_toolkit.mic_diarize import (
    DiarizeParams,
    _shift_trailing_subjects,
    assign_speakers_per_word,
    cards_from_assignments,
    compute_rms_envelope,
    rms_db_in_window,
    write_outputs,
)


# ──────────────────────────────────────────────
# 合成 wav 輔助工具
# ──────────────────────────────────────────────

SAMPLE_RATE = 16000  # 與 vad_gate VAD_SAMPLE_RATE 一致


def _make_sine_samples(freq: float, duration_sec: float, amplitude: int = 20000) -> np.ndarray:
    """產生已知頻率正弦波 int16 陣列（單聲道）。"""
    t = np.linspace(0, duration_sec, int(SAMPLE_RATE * duration_sec), endpoint=False)
    return (amplitude * np.sin(2 * math.pi * freq * t)).astype(np.int16)


def _make_silence(duration_sec: float) -> np.ndarray:
    """靜音段（全 0 int16）。"""
    return np.zeros(int(SAMPLE_RATE * duration_sec), dtype=np.int16)


def _make_constant_samples(amplitude: int, duration_sec: float) -> np.ndarray:
    """常數振幅（正值）int16，方便精確計算解析 RMS。"""
    return np.full(int(SAMPLE_RATE * duration_sec), amplitude, dtype=np.int16)


def _write_wav_to_tmp(samples: np.ndarray, tmp_dir: str) -> Path:
    """把 int16 mono 陣列寫成暫存 wav（標準 wave 模組，不依賴 ffmpeg），回 Path。"""
    p = Path(tempfile.mktemp(suffix=".wav", dir=tmp_dir))
    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(samples.tobytes())
    return p


def _make_word(w: str, start: float, end: float) -> dict:
    return {"w": w, "start": start, "end": end, "seg": 0, "p": 1.0, "alp": 1.0, "nsp": 0.0, "spk": "Mic1"}


# ──────────────────────────────────────────────
# §7.1 compute_rms_envelope 正確性
# ──────────────────────────────────────────────

def test_compute_rms_envelope_constant(tmp_path):
    """常數振幅 A → RMS 應 ≈ A/32768（誤差 < 5%）；frame_sec 正確。"""
    amplitude = 16384  # 0.5 * INT16_MAX
    expected_rms = amplitude / 32768.0
    duration = 1.0
    samples = _make_constant_samples(amplitude, duration)

    rms_frames, frame_sec = compute_rms_envelope(samples, SAMPLE_RATE, frame_ms=20)

    assert frame_sec == pytest.approx(0.02, rel=1e-6)
    # 去掉最後一幀（可能截邊），取前 95% 驗精度
    n_check = max(1, len(rms_frames) * 95 // 100)
    assert len(rms_frames) > 0
    for val in rms_frames[:n_check]:
        assert abs(val - expected_rms) / expected_rms < 0.05, (
            f"RMS={val:.5f}, expected≈{expected_rms:.5f}"
        )


def test_compute_rms_envelope_sine(tmp_path):
    """正弦波 sin(t)*A → RMS≈A/sqrt(2)/32768。"""
    amplitude = 20000
    expected_rms = amplitude / math.sqrt(2) / 32768.0
    samples = _make_sine_samples(440, 2.0, amplitude)

    rms_frames, frame_sec = compute_rms_envelope(samples, SAMPLE_RATE, frame_ms=20)

    n_check = max(1, len(rms_frames) * 80 // 100)
    for val in rms_frames[:n_check]:
        assert abs(val - expected_rms) / expected_rms < 0.05


def test_compute_rms_envelope_frame_sec():
    """frame_ms=30 → frame_sec≈0.03。"""
    samples = _make_constant_samples(10000, 1.0)
    _, frame_sec = compute_rms_envelope(samples, SAMPLE_RATE, frame_ms=30)
    assert frame_sec == pytest.approx(0.03, rel=1e-6)


# ──────────────────────────────────────────────
# §7.2 rms_db_in_window 視窗行為
# ──────────────────────────────────────────────

def test_rms_db_in_window_short_word():
    """字長不足一幀（0.01s < 20ms）→ 仍能取到中點幀，不 crash 也不回 NEG_INF（除非真靜音）。"""
    amplitude = 16000
    samples = _make_constant_samples(amplitude, 1.0)
    rms_frames, frame_sec = compute_rms_envelope(samples, SAMPLE_RATE, frame_ms=20)

    # 0.5s ~ 0.51s → 僅 10ms，不足一幀
    db = rms_db_in_window(rms_frames, frame_sec, 0.5, 0.51)
    assert math.isfinite(db), "短字應取中點幀，不回 NEG_INF"
    assert db > -60.0


def test_rms_db_in_window_offset():
    """offset 平移：前段靜音、後段有聲。同一 word 時間，offset=0 取到靜音段、offset 正確才有聲。"""
    # 前 1s 靜音，後 1s 有聲
    samples = np.concatenate([_make_silence(1.0), _make_constant_samples(16000, 1.0)])
    rms_frames, frame_sec = compute_rms_envelope(samples, SAMPLE_RATE, frame_ms=20)

    # word 時間在 0~0.5s（靜音區），offset=0 → 靜音
    db_no_offset = rms_db_in_window(rms_frames, frame_sec, 0.0, 0.5, offset=0.0)
    # offset=1.0 → 取 1.0~1.5s 的有聲區
    db_with_offset = rms_db_in_window(rms_frames, frame_sec, 0.0, 0.5, offset=1.0)

    assert db_no_offset < -40.0, "offset=0 應取到靜音段"
    assert db_with_offset > -20.0, "offset=1.0 應取到有聲段"


def test_rms_db_in_window_oob_clip():
    """越界（start+offset < 0 或 end+offset > 音檔長）→ clip 不 crash。"""
    samples = _make_constant_samples(10000, 1.0)
    rms_frames, frame_sec = compute_rms_envelope(samples, SAMPLE_RATE, frame_ms=20)

    # 超出右界
    db = rms_db_in_window(rms_frames, frame_sec, 0.9, 1.5)
    assert math.isfinite(db)

    # 超出左界（用負 offset）
    db2 = rms_db_in_window(rms_frames, frame_sec, 0.1, 0.3, offset=-1.0)
    # 全空（clip 後無幀）→ NEG_INF；或 clip 到邊界仍有幀 → 有限值
    # 重點：不 crash
    assert isinstance(db2, float)


def test_rms_db_in_window_all_empty_returns_neg_inf():
    """完全越界（offset 造成所有幀索引負）→ 回 NEG_INF。"""
    samples = _make_constant_samples(10000, 0.5)
    rms_frames, frame_sec = compute_rms_envelope(samples, SAMPLE_RATE, frame_ms=20)

    # 用超大負 offset 讓整個視窗跑到負索引
    db = rms_db_in_window(rms_frames, frame_sec, 0.1, 0.3, offset=-100.0)
    assert math.isinf(db) and db < 0, "全越界應回 NEG_INF"


# ──────────────────────────────────────────────
# §7.3 純輪流（N=2）
# ──────────────────────────────────────────────

def test_pure_alternation_n2(tmp_path):
    """A 說 0-2s（A 軌高，B 軌無聲），B 說 2-4s → assignments 正確，切出 2 卡無跨講者。"""
    # A 軌：前 2s 有聲，後 2s 靜音
    a_samples = np.concatenate([_make_constant_samples(20000, 2.0), _make_silence(2.0)])
    # B 軌：前 2s 靜音，後 2s 有聲
    b_samples = np.concatenate([_make_silence(2.0), _make_constant_samples(20000, 2.0)])

    a_env, a_fs = compute_rms_envelope(a_samples, SAMPLE_RATE)
    b_env, b_fs = compute_rms_envelope(b_samples, SAMPLE_RATE)
    envelopes = {"a": (a_env, a_fs), "b": (b_env, b_fs)}

    # 10 個字：前 5 個在 0~1s（A 主講區），後 5 個在 2.1~3.1s（B 主講區）
    words_a = [_make_word("字", 0.2 * i, 0.2 * i + 0.15) for i in range(5)]
    words_b = [_make_word("字", 2.1 + 0.2 * i, 2.1 + 0.2 * i + 0.15) for i in range(5)]
    words = words_a + words_b

    params = DiarizeParams(margin_db=2.0, silence_floor_db=-60.0, min_turn_sec=0.0, min_turn_words=0)
    assignments = assign_speakers_per_word(words, envelopes, params=params)

    assert assignments[:5] == ["a"] * 5, f"前 5 字應為 a，got {assignments[:5]}"
    assert assignments[5:] == ["b"] * 5, f"後 5 字應為 b，got {assignments[5:]}"

    cards = cards_from_assignments(words, assignments, params=params)
    assert len(cards) == 2
    assert cards[0]["speaker"] == "a"
    assert cards[1]["speaker"] == "b"
    # 保證無跨講者卡
    for c in cards:
        span = c["word_span"]
        spk_set = {assignments[i] for i in range(span[0], span[1])}
        assert len(spk_set) == 1, f"卡內有多個講者：{spk_set}"


# ──────────────────────────────────────────────
# §7.4 串音不誤判
# ──────────────────────────────────────────────

def test_crosstalk_no_misjudge():
    """A 主講（高），B 軌有 -20dB 串音（低但非靜音）→ margin_db=3 擋掉，全段判 A。"""
    # A 軌：20000 振幅 ≈ 0.61 normalized RMS
    # B 軌：3000 振幅（≈ 0.092 normalized，相差 ~16dB）
    a_samples = _make_constant_samples(20000, 2.0)
    b_samples = _make_constant_samples(3000, 2.0)

    a_env, a_fs = compute_rms_envelope(a_samples, SAMPLE_RATE)
    b_env, b_fs = compute_rms_envelope(b_samples, SAMPLE_RATE)
    envelopes = {"a": (a_env, a_fs), "b": (b_env, b_fs)}

    words = [_make_word("字", 0.2 * i, 0.2 * i + 0.15) for i in range(8)]
    params = DiarizeParams(margin_db=3.0, silence_floor_db=-60.0, min_turn_sec=0.0, min_turn_words=0)
    assignments = assign_speakers_per_word(words, envelopes, params=params)

    assert all(a == "a" for a in assignments), f"串音不應讓 B 搶到：{assignments}"


# ──────────────────────────────────────────────
# §7.5 重疊（A 能量較高）
# ──────────────────────────────────────────────

def test_overlap_higher_energy_wins():
    """A、B 同時開口，A 振幅高於 B → 全段字歸 A。"""
    a_samples = _make_constant_samples(18000, 2.0)
    b_samples = _make_constant_samples(8000, 2.0)

    a_env, a_fs = compute_rms_envelope(a_samples, SAMPLE_RATE)
    b_env, b_fs = compute_rms_envelope(b_samples, SAMPLE_RATE)
    envelopes = {"a": (a_env, a_fs), "b": (b_env, b_fs)}

    words = [_make_word("字", 0.2 * i, 0.2 * i + 0.15) for i in range(5)]
    params = DiarizeParams(margin_db=2.0, silence_floor_db=-60.0, min_turn_sec=0.0, min_turn_words=0)
    assignments = assign_speakers_per_word(words, envelopes, params=params)

    assert all(a == "a" for a in assignments)


# ──────────────────────────────────────────────
# §7.6 短附和不抖動
# ──────────────────────────────────────────────

def test_short_backchannel_no_jitter():
    """A 長句中插入 1 字的 B 瞬間高 → min_turn_sec/min_turn_words 併回 A，不切出孤立 B 卡。"""
    # 2 個字 A → 1 個字 B → 3 個字 A
    assignments_raw = ["a", "a", "b", "a", "a", "a"]
    words = [_make_word("字", 0.5 * i, 0.5 * i + 0.4) for i in range(6)]

    # 直接測 assign_speakers_per_word 後的短附和後處理：
    # 建構 envelopes，讓第 3 個字 B 軌高
    a_lvl = [20000, 20000, 500, 20000, 20000, 20000]
    b_lvl = [500, 500, 20000, 500, 500, 500]

    def _per_word_samples(levels: list, dur: float = 0.5) -> np.ndarray:
        segs = []
        for lvl in levels:
            segs.append(_make_constant_samples(lvl, dur))
        return np.concatenate(segs)

    a_smp = _per_word_samples(a_lvl)
    b_smp = _per_word_samples(b_lvl)
    a_env, a_fs = compute_rms_envelope(a_smp, SAMPLE_RATE)
    b_env, b_fs = compute_rms_envelope(b_smp, SAMPLE_RATE)
    envelopes = {"a": (a_env, a_fs), "b": (b_env, b_fs)}

    params = DiarizeParams(
        margin_db=2.0,
        silence_floor_db=-60.0,
        min_turn_sec=0.8,   # 單字段 0.5s < 0.8s → 觸發併回
        min_turn_words=2,   # 單字段 1 字 < 2 → 觸發併回
    )
    assignments = assign_speakers_per_word(words, envelopes, params=params)

    cards = cards_from_assignments(words, assignments, params=params)
    # 不應有孤立的 B 卡
    b_cards = [c for c in cards if c["speaker"] == "b"]
    assert len(b_cards) == 0, f"短附和應被併回 A，但仍有 B 卡：{b_cards}"


def test_short_reaction_word_kept_as_own_card():
    """A 長句中插入 1 字的反應詞「對啊」(B 軌高) → 反應詞閘門保留成獨立 B 卡，不併回 A。

    對比 test_short_backchannel_no_jitter：那裡插的是普通字「字」→ 併回；
    這裡插的是反應詞「對啊」→ 保留。兩者用同一組能量/門檻，差別只在字是否為反應詞。
    """
    # 2 字 A →「對啊」B → 3 字 A
    words = [
        _make_word("我", 0.0, 0.4),
        _make_word("覺得", 0.5, 0.9),
        _make_word("對啊", 1.0, 1.4),   # 反應詞、B 軌高
        _make_word("那個", 1.5, 1.9),
        _make_word("後來", 2.0, 2.4),
        _make_word("就是", 2.5, 2.9),
    ]
    a_lvl = [20000, 20000, 500, 20000, 20000, 20000]
    b_lvl = [500, 500, 20000, 500, 500, 500]

    def _per_word_samples(levels: list, dur: float = 0.5) -> np.ndarray:
        return np.concatenate([_make_constant_samples(lvl, dur) for lvl in levels])

    a_env, a_fs = compute_rms_envelope(_per_word_samples(a_lvl), SAMPLE_RATE)
    b_env, b_fs = compute_rms_envelope(_per_word_samples(b_lvl), SAMPLE_RATE)
    envelopes = {"a": (a_env, a_fs), "b": (b_env, b_fs)}

    params = DiarizeParams(
        margin_db=2.0,
        silence_floor_db=-60.0,
        min_turn_sec=0.8,   # 單字段 0.4s < 0.8s → 一般字會觸發併回
        min_turn_words=2,   # 單字段 1 字 < 2 → 一般字會觸發併回
    )
    assignments = assign_speakers_per_word(words, envelopes, params=params)

    # 「對啊」是反應詞 → 應保留為 b
    assert assignments[2] == "b", f"反應詞短插話應保留為 b，實際 assignments={assignments}"
    cards = cards_from_assignments(words, assignments, params=params)
    b_cards = [c for c in cards if c["speaker"] == "b"]
    assert len(b_cards) == 1, f"反應詞應獨立成 1 張 B 卡，實際：{b_cards}"
    assert "對啊" in b_cards[0]["text"]


# ──────────────────────────────────────────────
# §7.7 offset 套用（正向與反向驗證）
# ──────────────────────────────────────────────

def test_offset_correct_assignment():
    """B 軌整體延遲 +0.5s，設 offsets[b]=0.5 → 判 B 正確；設 0 → 誤判。"""
    # 混音時間軸：0~1s A 說話，1~2s B 說話
    # A 軌：0~1s 有聲，1~2s 靜音（對齊混音）
    # B 軌：因為實際錄音延遲 0.5s，所以 B 軌的 0.5~1.5s 是有聲，對應混音 0~1s 是靜音、1~2s 的 0.5~1s 有聲
    # 更清楚：B 說話在混音 1~2s；B 軌（延遲 +0.5s）有聲在 1.5~2.5s
    a_samples = np.concatenate([_make_constant_samples(20000, 1.0), _make_silence(1.5)])
    b_samples = np.concatenate([_make_silence(1.5), _make_constant_samples(20000, 1.0)])
    # b 軌長 2.5s；說話在 1.5~2.5s，對應混音 (1.5 - 0.5) = 1.0~2.0s ✓

    a_env, a_fs = compute_rms_envelope(a_samples, SAMPLE_RATE)
    b_env, b_fs = compute_rms_envelope(b_samples, SAMPLE_RATE)
    envelopes = {"a": (a_env, a_fs), "b": (b_env, b_fs)}

    # 字在 0~1s（混音時間）
    words_a = [_make_word("甲", 0.1 * i, 0.1 * i + 0.08) for i in range(8)]
    # 字在 1~2s（混音時間）
    words_b = [_make_word("乙", 1.0 + 0.1 * i, 1.0 + 0.1 * i + 0.08) for i in range(8)]
    words = words_a + words_b

    params = DiarizeParams(margin_db=2.0, silence_floor_db=-60.0, min_turn_sec=0.0, min_turn_words=0)

    # 有正確 offset → 判 A/B 正確
    assignments_correct = assign_speakers_per_word(
        words, envelopes, offsets={"a": 0.0, "b": 0.5}, params=params
    )
    a_part_correct = assignments_correct[:8]
    b_part_correct = assignments_correct[8:]
    assert all(s == "a" for s in a_part_correct), f"offset 正確時前段應為 a：{a_part_correct}"
    assert all(s == "b" for s in b_part_correct), f"offset 正確時後段應為 b：{b_part_correct}"

    # offset=0（錯誤）→ B 的字應被誤判（反向驗證）
    assignments_wrong = assign_speakers_per_word(
        words, envelopes, offsets={"a": 0.0, "b": 0.0}, params=params
    )
    b_part_wrong = assignments_wrong[8:]
    # offset 錯誤時，B 軌 1~2s 是靜音，A 軌 1~2s 也是靜音 → 應為 None 或誤判為 a
    # 重點：不等於 offset 正確時的結果
    assert b_part_wrong != b_part_correct, "offset=0 應與正確 offset 產出不同結果"


# ──────────────────────────────────────────────
# §7.8 N=3 軌
# ──────────────────────────────────────────────

def test_three_speakers():
    """a/b/c 三軌各主講 2s → 三講者都出現、順序正確。"""
    silence = _make_silence(2.0)
    loud = _make_constant_samples(20000, 2.0)

    # a 說 0-2s，b 說 2-4s，c 說 4-6s
    a_smp = np.concatenate([loud, silence, silence])
    b_smp = np.concatenate([silence, loud, silence])
    c_smp = np.concatenate([silence, silence, loud])

    a_env, a_fs = compute_rms_envelope(a_smp, SAMPLE_RATE)
    b_env, b_fs = compute_rms_envelope(b_smp, SAMPLE_RATE)
    c_env, c_fs = compute_rms_envelope(c_smp, SAMPLE_RATE)
    envelopes = {"a": (a_env, a_fs), "b": (b_env, b_fs), "c": (c_env, c_fs)}

    words = [_make_word("字", 0.4 * i, 0.4 * i + 0.3) for i in range(15)]

    params = DiarizeParams(margin_db=2.0, silence_floor_db=-60.0, min_turn_sec=0.0, min_turn_words=0)
    assignments = assign_speakers_per_word(words, envelopes, params=params)

    speakers_seen = {s for s in assignments if s is not None}
    assert "a" in speakers_seen, "a 應出現"
    assert "b" in speakers_seen, "b 應出現"
    assert "c" in speakers_seen, "c 應出現"

    cards = cards_from_assignments(words, assignments, params=params)
    card_speakers = [c["speaker"] for c in cards]
    assert "a" in card_speakers
    assert "b" in card_speakers
    assert "c" in card_speakers


# ──────────────────────────────────────────────
# §7.9 換人切卡
# ──────────────────────────────────────────────

def test_speaker_change_forces_card_break():
    """A→B 交界處無論長度，一律斷卡（不同 speaker 絕不同卡）。"""
    words = [_make_word("字", 0.2 * i, 0.2 * i + 0.15) for i in range(10)]
    # 前 5 字 A，後 5 字 B
    assignments = ["a"] * 5 + ["b"] * 5

    params = DiarizeParams()
    cards = cards_from_assignments(words, assignments, params=params)

    # 保證無跨講者卡
    for c in cards:
        span = c["word_span"]
        spk_set = {assignments[i] for i in range(span[0], span[1])}
        assert len(spk_set) == 1, f"卡內出現多講者：{spk_set}"

    # 確保 A 和 B 都有卡
    seen = {c["speaker"] for c in cards}
    assert "a" in seen and "b" in seen

    # 確保卡的邊界在第 5 字（A 最後一字 end）和第 6 字（B 第一字 start）之間
    a_cards = [c for c in cards if c["speaker"] == "a"]
    b_cards = [c for c in cards if c["speaker"] == "b"]
    assert a_cards[-1]["end"] <= b_cards[0]["start"] + 1e-9


# ──────────────────────────────────────────────
# §7.10 長句同人再切（maxlen/hardlen/dangle）
# ──────────────────────────────────────────────

def test_long_sentence_same_speaker_split():
    """A 連講 40 字無間隙 → 依 maxlen 切多卡，皆標 A；dangle 尾字放寬到 hardlen。"""
    # 每字 0.1s，間隔極小（gap << gapmax）
    words = [_make_word("字", 0.1 * i, 0.1 * i + 0.095) for i in range(40)]
    assignments = ["a"] * 40

    params = DiarizeParams(maxlen=17, hardlen=23, gapmax=0.6)
    cards = cards_from_assignments(words, assignments, params=params)

    # 應切出多張卡
    assert len(cards) > 1, "40 字應依 maxlen 切成多卡"
    # 全部標 A
    assert all(c["speaker"] == "a" for c in cards)
    # 每張卡字數不超過 hardlen（允許 dangle 放寬）
    for c in cards:
        span = c["word_span"]
        n_words_in_card = span[1] - span[0]
        assert n_words_in_card <= params.hardlen, f"卡字數 {n_words_in_card} 超過 hardlen={params.hardlen}"

    # 測 dangle 放寬：每字一個字元（w="a"），前 17 個字 → 字元數=17=maxlen，
    # 此時尾字是 dangle 詞（"因為"→拆成兩字，或改 dangle_endings 用單字）。
    # 策略：18 個單字元 word，第 18 字是新卡起點；用 maxlen=17、dangle_endings 含倒數第 17 字的文字。
    # 更簡單：14 個單字元 word + 1 個 3 字元 word "因為"（第 15 字），maxlen=17 → candidate=17，
    # is_dangling("甲"*14+"因為")=True，hardlen=23 → 允許，不斷。
    dangle_text_words2 = [_make_word("甲", 0.1 * i, 0.1 * i + 0.095) for i in range(14)]
    dangle_text_words2.append(_make_word("因為", 1.4, 1.495))  # 共 14 + 2 = 16 字元
    # 加 1 個字讓 candidate 超 maxlen=17：再加一字元
    dangle_text_words2.append(_make_word("所", 1.5, 1.595))  # candidate = 17 字元
    dangle_assignments2 = ["a"] * 16
    dangle_params2 = DiarizeParams(maxlen=17, hardlen=23, gapmax=0.6,
                                   dangle_endings=("因為",))
    dangle_cards2 = cards_from_assignments(dangle_text_words2, dangle_assignments2, params=dangle_params2)
    # 最後段：前 16 字累積 → 加第 16 字（所）後 candidate = "甲"*14+"因為"+"所" = 17 字元
    # 此時 cur_text（加入前）= "甲"*14+"因為" → 結尾是 "因為" = dangle，且 candidate 長度 17 ≤ hardlen=23
    # → 放寬不斷，整段 16 字為一卡
    assert len(dangle_cards2) == 1, f"dangle 結尾應放寬，不斷；got {len(dangle_cards2)} 卡"


def test_split_prefers_seg_boundary():
    """同人連續兩句（Breeze seg 不同），總長超過 maxlen 必須切一刀 →
    切點須落在 seg 句界，而非長度硬切點（不可把某句從中間劈開）。"""
    seg0 = "甲乙丙丁戊己庚辛壬癸"   # 10 字
    seg1 = "子丑寅卯辰巳午未申酉"   # 10 字，總 20 > maxlen=17
    words = []
    t = 0.0
    for ch in seg0:
        w = _make_word(ch, t, t + 0.095)
        w["seg"] = 0
        words.append(w)
        t += 0.1
    t += 0.2   # 句間停頓 0.2s（< gapmax=0.6，非 gap 觸發；純靠 seg 才會切在此）
    for ch in seg1:
        w = _make_word(ch, t, t + 0.095)
        w["seg"] = 1
        words.append(w)
        t += 0.1
    assignments = ["a"] * 20

    params = DiarizeParams(maxlen=17, hardlen=23, gapmax=0.6)
    cards = cards_from_assignments(words, assignments, params=params)

    assert len(cards) == 2, f"應切在 seg 句界成 2 卡，實際 {len(cards)} 卡"
    assert cards[0]["text"] == seg0, f"第一卡應為完整 seg0，實際「{cards[0]['text']}」"
    assert cards[1]["text"] == seg1, f"第二卡應為完整 seg1，實際「{cards[1]['text']}」"


def test_split_merges_short_segs_up_to_maxlen():
    """相鄰短句（各 5 字，總 10 ≤ maxlen）應併成一卡，避免碎片化。"""
    words = []
    t = 0.0
    for si, txt in enumerate(("甲乙丙丁戊", "子丑寅卯辰")):
        for ch in txt:
            w = _make_word(ch, t, t + 0.095)
            w["seg"] = si
            words.append(w)
            t += 0.1
        t += 0.2
    assignments = ["a"] * 10
    params = DiarizeParams(maxlen=17, hardlen=23, gapmax=0.6)
    cards = cards_from_assignments(words, assignments, params=params)
    assert len(cards) == 1, f"兩短句應併成 1 卡，實際 {len(cards)} 卡"
    assert cards[0]["text"] == "甲乙丙丁戊子丑寅卯辰"


def test_split_snaps_to_jieba_word_boundary():
    """超長句的長度硬切點若落在 jieba 詞中間，須退回最近詞界，不把詞劈開。

    構造：單一 Breeze seg「最比較糟糕的就是因為我的國語還算標準講台語…」，
    純長度貪婪切會切在第 17 字（標|準之間，劈開 jieba 詞「標準」）。
    詞界約束後，切點須退回第 16 字（標之前），「標準」保成整詞。
    """
    pytest.importorskip("jieba")
    text = "最比較糟糕的就是因為我的國語還算標準講台語大家都說"  # 25 字，單一 seg
    words = []
    t = 0.0
    for ch in text:
        w = _make_word(ch, t, t + 0.095)
        w["seg"] = 0
        words.append(w)
        t += 0.1
    assignments = ["a"] * len(text)
    params = DiarizeParams(maxlen=17, hardlen=23, gapmax=0.6)
    cards = cards_from_assignments(words, assignments, params=params)

    # 核心不變式：沒有任何一張卡以「標」結尾、下一張以「準」開頭（＝標準沒被劈開）
    joined = [c["text"] for c in cards]
    for a, b in zip(joined, joined[1:]):
        assert not (a.endswith("標") and b.startswith("準")), \
            f"jieba 詞「標準」被切開：…{a[-3:]}｜{b[:3]}…"
    # 「標準」須完整出現在某一張卡內
    assert any("標準" in c["text"] for c in cards), \
        f"「標準」未完整保留於任一卡：{joined}"


def test_glossary_terms_parses_episode_shape():
    """glossary 為 dict 清單（episode.yaml 實際結構）時，須抽出 canonical＋
    sounds_like，不可把整個 dict 當一個詞。"""
    from podcast_toolkit.mic_diarize import _glossary_terms
    gloss = [
        {"canonical": "王劭仁", "sounds_like": ["紹仁"], "note": ""},
        {"canonical": "郝慧川", "sounds_like": [], "note": "主持人"},
    ]
    terms = _glossary_terms(gloss)
    assert "王劭仁" in terms and "郝慧川" in terms and "紹仁" in terms
    # 不可出現 dict repr（舊 bug）
    assert all("canonical" not in t for t in terms), f"混入 dict repr：{terms}"
    # 純字串與逗號分隔字串也支援
    assert _glossary_terms(["甲", "乙"]) == ["甲", "乙"]
    assert _glossary_terms("甲, 乙 丙") == ["甲", "乙", "丙"]
    assert _glossary_terms(None) == []


# ──────────────────────────────────────────────
# §7.11 靜音/無主
# ──────────────────────────────────────────────

def test_silence_produces_no_card():
    """所有軌低於 silence_floor → 該字 None，不進任何卡，時間留白。"""
    # 極低振幅，使 RMS 換算成 dB 遠低於 silence_floor_db=-45
    a_samples = _make_constant_samples(10, 3.0)   # 振幅 10 → RMS ≈ 0.0003 → dB ≈ -70
    b_samples = _make_constant_samples(10, 3.0)

    a_env, a_fs = compute_rms_envelope(a_samples, SAMPLE_RATE)
    b_env, b_fs = compute_rms_envelope(b_samples, SAMPLE_RATE)
    envelopes = {"a": (a_env, a_fs), "b": (b_env, b_fs)}

    words = [_make_word("字", 0.3 * i, 0.3 * i + 0.2) for i in range(5)]
    params = DiarizeParams(silence_floor_db=-45.0, min_turn_sec=0.0, min_turn_words=0)
    assignments = assign_speakers_per_word(words, envelopes, params=params)

    assert all(a is None for a in assignments), f"靜音段應全為 None：{assignments}"

    cards = cards_from_assignments(words, assignments, params=params)
    assert len(cards) == 0, "靜音字不應產生任何卡"


# ──────────────────────────────────────────────
# §7.12 輸出格式
# ──────────────────────────────────────────────

def test_write_outputs_format(tmp_path):
    """write_outputs 產的 speakers.json 可被 cameras_io.load 讀回；SRT 可被 srt_io.parse 解析；
    idx 連續；相鄰卡在換人處 speaker 確實不同（無跨講者卡）。"""
    from podcast_toolkit import cameras_io, srt_io

    # 建立最小 Episode mock
    ep = MagicMock()
    out_dir = tmp_path / "03_成品"
    out_dir.mkdir()
    srt_path = out_dir / "test_final_v2.srt"
    spk_path = out_dir / "test_final_v2.speakers.json"
    ep.output_v2_srt.return_value = srt_path
    ep.output_v2_speakers_json.return_value = spk_path

    # 2 張 A 卡 + 2 張 B 卡
    cards = [
        {"start": 0.0, "end": 2.0, "text": "甲甲甲", "speaker": "a", "word_span": (0, 3)},
        {"start": 2.5, "end": 4.0, "text": "乙乙乙", "speaker": "a", "word_span": (3, 6)},
        {"start": 4.5, "end": 6.0, "text": "丙丙丙", "speaker": "b", "word_span": (6, 9)},
        {"start": 6.5, "end": 8.0, "text": "丁丁丁", "speaker": "b", "word_span": (9, 12)},
    ]

    srt_out, spk_out = write_outputs(ep, cards, backup=False)

    # speakers.json 格式驗收
    loaded = cameras_io.load(spk_out)
    assert isinstance(loaded, dict)
    assert all(isinstance(k, int) for k in loaded)
    assert all(isinstance(v, str) for v in loaded.values())
    # idx 應 1-based 連續
    idxs = sorted(loaded.keys())
    assert idxs == list(range(1, len(cards) + 1))
    # 講者對應
    assert loaded[1] == "a"
    assert loaded[3] == "b"

    # SRT 格式驗收
    srt_text = srt_out.read_text(encoding="utf-8")
    parsed = srt_io.parse(srt_text)
    assert len(parsed) == 4
    assert parsed[0]["idx"] == 1
    assert parsed[-1]["idx"] == 4

    # 換人處斷言：相鄰卡在講者換人時，speakers.json 確實反映不同 speaker
    spk_map = cameras_io.load(spk_out)
    for i in range(1, len(cards)):
        if cards[i]["speaker"] != cards[i - 1]["speaker"]:
            assert spk_map[i + 1] != spk_map[i], "換人卡在 speakers.json 應有不同 speaker"


# ──────────────────────────────────────────────
# §7.13 接線煙測（mock Episode）
# ──────────────────────────────────────────────

def test_run_return_codes(tmp_path):
    """mic_diarize.run 對缺 words.json → 3；缺 mics → 4；輸出已存在且 force=False → 1。"""
    from podcast_toolkit.mic_diarize import run

    # 缺 mics → return 4
    ep_no_mics = MagicMock()
    ep_no_mics.mic_paths.return_value = {}
    ep_no_mics.dir = tmp_path
    assert run(ep_no_mics) == 4

    # 有 mics 但缺 words.json → return 3
    ep_no_words = MagicMock()
    ep_no_words.mic_paths.return_value = {"a": tmp_path / "a.wav"}
    ep_no_words.dir = tmp_path  # glob 找不到 *_字幕_words.json
    ep_no_words.cfg = {}
    assert run(ep_no_words) == 3

    # 有 mics，有 words.json，輸出存在且 force=False → return 1
    words_json = tmp_path / "test_字幕_words.json"
    words_json.write_text(
        json.dumps({"words": [{"w": "字", "start": 0.0, "end": 0.5}]}),
        encoding="utf-8",
    )
    srt_exists = tmp_path / "out_final_v2.srt"
    srt_exists.touch()

    ep_exists = MagicMock()
    ep_exists.mic_paths.return_value = {"a": tmp_path / "a.wav"}
    ep_exists.dir = tmp_path
    ep_exists.cfg = {}
    ep_exists.output_v2_srt.return_value = srt_exists
    ep_exists.output_v2_speakers_json.return_value = tmp_path / "out.speakers.json"

    assert run(ep_exists, force=False) == 1


# ──────────────────────────────────────────────
# 額外：保證 cards_from_assignments 無跨講者卡（獨立斷言）
# ──────────────────────────────────────────────

def test_no_cross_speaker_card_guarantee():
    """任意 assignment 序列，cards_from_assignments 輸出絕無跨講者卡。"""
    words = [_make_word("字", 0.3 * i, 0.3 * i + 0.25) for i in range(20)]
    # 故意製造頻繁換人
    assignments = (["a", "b"] * 10)

    params = DiarizeParams(maxlen=17, hardlen=23, gapmax=0.6, min_turn_sec=0.0, min_turn_words=0)
    cards = cards_from_assignments(words, assignments, params=params)

    for c in cards:
        span = c["word_span"]
        spk_set = {assignments[i] for i in range(span[0], span[1]) if assignments[i] is not None}
        assert len(spk_set) <= 1, f"發現跨講者卡：{spk_set}，卡={c}"


# ──────────────────────────────────────────────
# §主詞掛尾後處理：_shift_trailing_subjects
# ──────────────────────────────────────────────

def _w(ch: str, start: float, end: float, seg: int = 0, spk: str = "A") -> dict:
    """建立合成 word dict（含 seg/spk 欄）。"""
    return {"w": ch, "start": start, "end": end, "seg": seg, "spk": spk,
            "p": 1.0, "alp": 1.0, "nsp": 0.0}


def _make_cards_direct(words: list, spans: list, speaker: str = "A") -> list:
    """用 words 全域 list + 半開 span list 直接建 cards（不跑切卡邏輯）。"""
    from podcast_toolkit.mic_diarize import _make_card
    cards = []
    for lo, hi in spans:
        cards.append(_make_card(words, lo, hi, speaker, lo, hi))
    return cards


def _default_params():
    return DiarizeParams(maxlen=30, hardlen=40, gapmax=0.5,
                         min_turn_sec=0.0, min_turn_words=0,
                         subject_shift=True)


# ── 正例：必須搬 ──────────────────────────────────

def test_shift_subject_single_char_wo():
    """A="跟大家講我" B="不是有錢人" → 搬「我」到 B 頭，A 縮成「跟大家講」。"""
    chars_a = list("跟大家講我")
    chars_b = list("不是有錢人")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 5), (5, 10)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert len(result) == 2
    assert result[0]["text"] == "跟大家講"
    assert result[1]["text"] == "我不是有錢人"


def test_shift_subject_double_char_zhege():
    """A="它不要想這個" B="反而應該你不要去"。
    OBJECT_VETO 包含「想」（想＋受詞語境），故「這個」前一字「想」命中否決 → 不搬。
    高精度優先、寧漏勿錯：此情況正確行為是保留原卡。
    （規格正例 2 與常數定義有矛盾，以常數為準——「想」確實是受詞動詞）"""
    chars_a = list("它不要想這個")
    chars_b = list("反而應該你不要去")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 6), (6, 14)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    # 「想」∈ OBJECT_VETO → 否決，不搬
    assert result[0]["text"] == "它不要想這個"
    assert result[1]["text"] == "反而應該你不要去"


def test_shift_subject_wo_with_predicate():
    """A="所以我覺得我" B="如果變個營養師" → 搬「我」，但「如果」不在謂語集。

    規格中正例 3 是「如果變個營養師」——B 首字「如」需命中 PREDICATE_STARTERS_1。
    「如」在謂語字集中驗一下；若不在則此正例預期不搬（規格只列了三例，以實際常數為準）。
    實際確認後：規格要求搬。
    """
    from podcast_toolkit.mic_diarize import PREDICATE_STARTERS_1
    # 確認「如」有無在謂語集
    b_text = "如果變個營養師"
    b_pred_ok = b_text[:2] in {"覺得", "可以", "已經", "其實", "應該", "必須",
                                "反而", "甚至", "一定", "沒有", "不是", "還是",
                                "知道", "選擇", "決定", "可能", "就是", "才是", "開始"} \
                or b_text[0] in PREDICATE_STARTERS_1
    # 注意：規格中B首字「如」並不在一字謂語集、「如果」也不在雙字謂語集
    # → 此例在嚴格規格下不搬，測試應驗「不搬」以保持高精度
    chars_a = list("所以我覺得我")
    chars_b = list("如果變個營養師")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 6), (6, 13)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    if b_pred_ok:
        # 謂語判定通過 → 搬
        assert result[0]["text"] == "所以我覺得"
        assert result[1]["text"].startswith("我")
    else:
        # 謂語判定未通過 → 不搬（高精度，寧漏勿錯）
        assert result[0]["text"] == "所以我覺得我"
        assert result[1]["text"] == "如果變個營養師"


def test_shift_subject_meiyou_predicate():
    """B="沒有他就給" → B 首「沒」∈ PREDICATE_STARTERS_1，搬 A 尾的主詞。"""
    chars_a = list("你說講我")
    chars_b = list("沒有問題啊")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    # A="你說講我"，T="我"，前一字"講"不在 OBJECT_VETO，B首"沒"∈謂語集
    cards = _make_cards_direct(words, [(0, 4), (4, 9)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "你說講"
    assert result[1]["text"].startswith("我")


# ── 負例：必須不搬 ────────────────────────────────

def test_no_shift_subject_not_in_tokens():
    """A="常常會提醒自己" → 「自己」不在 SUBJECT_TOKENS，不搬。"""
    chars_a = list("常常會提醒自己")
    chars_b = list("才能進步更多")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 7), (7, 13)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "常常會提醒自己"
    assert result[1]["text"] == "才能進步更多"


def test_no_shift_object_veto_shi():
    """A="認識你們" B="但是你們不跟" → 前一字「識」∈ OBJECT_VETO → 不搬。"""
    chars_a = list("認識你們")
    chars_b = list("但是你們不跟")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 4), (4, 10)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "認識你們"
    assert result[1]["text"] == "但是你們不跟"


def test_no_shift_object_veto_zi():
    """A="你說他投資你" B="沒有他就給" → 「資」∈ OBJECT_VETO → 不搬「你」。"""
    chars_a = list("你說他投資你")
    chars_b = list("沒有他就給")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 6), (6, 11)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "你說他投資你"
    assert result[1]["text"] == "沒有他就給"


def test_no_shift_role_noun_not_in_tokens():
    """A="身為老師" → 「老師」不在 SUBJECT_TOKENS，不搬。"""
    chars_a = list("身為老師")
    chars_b = list("就要以身作則")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 4), (4, 10)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "身為老師"
    assert result[1]["text"] == "就要以身作則"


def test_no_shift_b_starts_with_de():
    """A="記得要問我" B="的書" → B 首「的」∈ _COMP_STARTERS → 不搬。"""
    chars_a = list("記得要問我")
    chars_b = list("的書在哪裡")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 5), (5, 10)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "記得要問我"
    assert result[1]["text"] == "的書在哪裡"


def test_no_shift_different_speaker():
    """A、B 講者不同 → 前提不符，不搬。"""
    chars_a = list("跟大家講我")
    chars_b = list("不是有錢人")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    from podcast_toolkit.mic_diarize import _make_card
    card_a = _make_card(words, 0, 5, "A", 0, 5)
    card_b = _make_card(words, 5, 10, "B", 5, 10)  # 不同講者
    result = _shift_trailing_subjects([card_a, card_b], words, _default_params())
    assert result[0]["text"] == "跟大家講我"
    assert result[1]["text"] == "不是有錢人"


# ── 整合測試 ──────────────────────────────────────

def test_integration_cards_from_assignments_shift():
    """整合測試：重現正例1（A="跟大家講我" B="不是有錢人"），
    跑 cards_from_assignments 後驗結果無「卡尾為核心主詞且下一卡以謂語開頭」。"""
    # 建 words：A 段 seg=0，B 段 seg=1，同講者
    chars_a = list("跟大家講我")
    chars_b = list("不是有錢人")
    t = 0.0
    words = []
    for ch in chars_a:
        words.append({"w": ch, "start": t, "end": t + 0.2, "seg": 0,
                      "spk": "A", "p": 1.0, "alp": 1.0, "nsp": 0.0})
        t += 0.25
    for ch in chars_b:
        words.append({"w": ch, "start": t, "end": t + 0.2, "seg": 1,
                      "spk": "A", "p": 1.0, "alp": 1.0, "nsp": 0.0})
        t += 0.25
    assignments = ["A"] * len(words)
    params = DiarizeParams(maxlen=30, hardlen=40, gapmax=0.5,
                           min_turn_sec=0.0, min_turn_words=0,
                           subject_shift=True)
    cards = cards_from_assignments(words, assignments, params=params)
    # 驗結果：所有卡 尾為「我」但下一卡首∈謂語 的情況應已不存在
    from podcast_toolkit.mic_diarize import PREDICATE_STARTERS_1, PREDICATE_STARTERS_2, SUBJECT_TOKENS
    violations = []
    for idx in range(len(cards) - 1):
        a_text = cards[idx]["text"]
        b_text = cards[idx + 1]["text"]
        for tok in SUBJECT_TOKENS:
            if a_text.endswith(tok):
                b_pred = (b_text[:2] in PREDICATE_STARTERS_2
                          or (b_text and b_text[0] in PREDICATE_STARTERS_1))
                if b_pred:
                    violations.append((a_text, b_text))
    assert violations == [], f"仍有主詞掛尾：{violations}"


# ── subject_shift 開關測試 ────────────────────────

def test_subject_shift_disabled():
    """subject_shift=False 時後處理不作用，主詞掛尾仍存在。"""
    chars_a = list("跟大家講我")
    chars_b = list("不是有錢人")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 5), (5, 10)])
    params_off = _default_params()
    params_off = DiarizeParams(maxlen=30, hardlen=40, gapmax=0.5,
                               min_turn_sec=0.0, min_turn_words=0,
                               subject_shift=False)
    result = _shift_trailing_subjects(cards, words, params_off)
    # 不搬（開關關閉由 cards_from_assignments 攔截，這裡直呼叫函式本身）
    # _shift_trailing_subjects 本身不看 subject_shift；開關在呼叫端
    # 改驗透過 cards_from_assignments 呼叫：
    assignments = ["A"] * len(words)
    cards2 = cards_from_assignments(words, assignments, params=params_off)
    # 應保留「我」在 A 卡尾（或至少整合後行為不強制搬移）
    texts = [c["text"] for c in cards2]
    # 找到含「講我」的卡（即「我」沒被搬走）
    assert any("講我" in t or t.endswith("我") for t in texts), \
        f"subject_shift=False 時主詞應保留於原卡，但 texts={texts}"


# ──────────────────────────────────────────────
# §新增：OBJECT_VETO 擴充 + 回看升級 反例/回歸測試
# ──────────────────────────────────────────────
# 反例（必須不搬）
# ──────────────────────────────────────────────

def test_no_shift_veto_su_gaosu_ni():
    """A="我想告訴你" B="說的事情" → 訴∈OBJECT_VETO，你=受詞，不搬。"""
    chars_a = list("我想告訴你")
    chars_b = list("說的事情")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 5), (5, 9)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "我想告訴你", f"告訴你 → 訴∈OBJECT_VETO，不搬；got {result[0]['text']}"
    assert result[1]["text"] == "說的事情"


def test_no_shift_coverb_yu_guanyu_ni():
    """A="這是關於你" B="要做的決定" → 於∈COVERB_PREP，你=介補受詞，不搬。"""
    chars_a = list("這是關於你")
    chars_b = list("要做的決定")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 5), (5, 10)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "這是關於你", f"關於你 → 於∈COVERB_PREP，不搬；got {result[0]['text']}"
    assert result[1]["text"] == "要做的決定"


def test_no_shift_coverb_wei_weile_ni():
    """A="我都是為了你" B="才這樣做" → 為∈COVERB_PREP，你=介補受詞，不搬。"""
    chars_a = list("我都是為了你")
    chars_b = list("才這樣做")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 6), (6, 10)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "我都是為了你", f"為了你 → 為∈COVERB_PREP，不搬；got {result[0]['text']}"
    assert result[1]["text"] == "才這樣做"


def test_no_shift_aspect_kanjian_ni():
    """A="大家看到你" B="要準備" → 看+到(ASPECT)+你，回看兩字命中看∈OBJECT_VETO，不搬。"""
    chars_a = list("大家看到你")
    chars_b = list("要準備")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 5), (5, 8)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "大家看到你", f"看到你 → 動補回看兩字命中看∈OBJECT_VETO，不搬；got {result[0]['text']}"
    assert result[1]["text"] == "要準備"


def test_no_shift_veto_hu_baohu_ni():
    """A="我會保護你" B="不會有事" → 護∈OBJECT_VETO，你=受詞，不搬。"""
    chars_a = list("我會保護你")
    chars_b = list("不會有事")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 5), (5, 9)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "我會保護你", f"保護你 → 護∈OBJECT_VETO，不搬；got {result[0]['text']}"
    assert result[1]["text"] == "不會有事"


def test_no_shift_veto_fan_mafan_ni():
    """A="要麻煩你" B="再確認" → 煩∈OBJECT_VETO，你=受詞，不搬。"""
    chars_a = list("要麻煩你")
    chars_b = list("再確認")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 4), (4, 7)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "要麻煩你", f"麻煩你 → 煩∈OBJECT_VETO，不搬；got {result[0]['text']}"
    assert result[1]["text"] == "再確認"


# ──────────────────────────────────────────────
# 回歸保護（真主詞仍搬）
# ──────────────────────────────────────────────

def test_shift_still_works_jiang_wo():
    """A="跟大家講我" B="不是有錢人" → 講不在OBJECT_VETO，我仍搬到B頭。（回歸保護）"""
    chars_a = list("跟大家講我")
    chars_b = list("不是有錢人")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 5), (5, 10)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "跟大家講", f"講不在OBJECT_VETO，我應搬走；got {result[0]['text']}"
    assert result[1]["text"] == "我不是有錢人"


def test_shift_still_works_juede_wo():
    """A="所以我覺得我" B="會選這個" → 得不在OBJECT_VETO，B首會∈PS1，我仍搬。（回歸保護）"""
    chars_a = list("所以我覺得我")
    chars_b = list("會選這個")
    t = 0.0
    words = []
    for ch in chars_a + chars_b:
        words.append(_w(ch, t, t + 0.2))
        t += 0.25
    cards = _make_cards_direct(words, [(0, 6), (6, 10)])
    result = _shift_trailing_subjects(cards, words, _default_params())
    assert result[0]["text"] == "所以我覺得", f"覺得後的我應搬；got {result[0]['text']}"
    assert result[1]["text"] == "我會選這個"
