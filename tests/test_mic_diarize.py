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
