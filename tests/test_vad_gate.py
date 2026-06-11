"""VAD gate：RMS 閾值 + min_speech + pad，把串音壓成靜音。

不測 ffmpeg I/O（那層交給 audio_align 同款 subprocess pattern）；
這裡聚焦純 numpy 演算法層，用合成訊號驗證行為。
"""
from __future__ import annotations

import numpy as np
import pytest

from podcast_toolkit import vad_gate


SR = 16000  # 全測試固定 16kHz mono


def _silence(duration_sec: float) -> np.ndarray:
    return np.zeros(int(SR * duration_sec), dtype=np.int16)


def _tone(duration_sec: float, amp: int = 8000, freq: int = 440) -> np.ndarray:
    """產合成正弦波當「說話」訊號。amp=8000 對 int16 約 -12dBFS。"""
    n = int(SR * duration_sec)
    t = np.arange(n) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


# --- detect_speech_frames：frame-level RMS 閾值判斷 ---


def test_detect_speech_frames_pure_silence_returns_all_false():
    samples = _silence(1.0)
    speech = vad_gate.detect_speech_frames(samples, SR, frame_ms=20, threshold=0.02)
    assert speech.dtype == bool
    assert not speech.any()


def test_detect_speech_frames_loud_tone_returns_all_true():
    samples = _tone(1.0, amp=16000)  # ~-6dBFS，遠高於 0.02 threshold
    speech = vad_gate.detect_speech_frames(samples, SR, frame_ms=20, threshold=0.02)
    assert speech.all()


def test_detect_speech_frames_quiet_tone_below_threshold_returns_false():
    """振幅 ~50 → normalized RMS ≈ 0.001，遠低於 0.02 threshold。"""
    samples = _tone(1.0, amp=50)
    speech = vad_gate.detect_speech_frames(samples, SR, frame_ms=20, threshold=0.02)
    assert not speech.any()


def test_detect_speech_frames_silence_then_speech_then_silence():
    """0.5s 靜音 + 0.5s 音 + 0.5s 靜音 → 中段為 True。"""
    samples = np.concatenate([_silence(0.5), _tone(0.5, amp=16000), _silence(0.5)])
    speech = vad_gate.detect_speech_frames(samples, SR, frame_ms=20, threshold=0.02)
    n_frames = len(speech)
    # 中段 1/3 應該全 True，頭尾 1/3 全 False（容忍 frame 邊界誤差）
    third = n_frames // 3
    assert not speech[: third - 2].any()
    assert speech[third + 2 : 2 * third - 2].all()
    assert not speech[2 * third + 2 :].any()


# --- apply_min_duration：壓掉太短的爆音 ---


def test_apply_min_duration_drops_burst_shorter_than_threshold():
    """單一 frame 的 True（爆音）→ 被壓掉。"""
    speech = np.zeros(100, dtype=bool)
    speech[50] = True  # 單 frame，20ms < 300ms min_speech
    out = vad_gate.apply_min_duration(
        speech, frame_samples=320, sample_rate=SR, min_speech_sec=0.3
    )
    assert not out.any()


def test_apply_min_duration_keeps_segment_longer_than_threshold():
    """連續 30 個 frame (600ms) > 300ms → 保留。"""
    speech = np.zeros(100, dtype=bool)
    speech[20:50] = True  # 30 frames * 20ms = 600ms
    out = vad_gate.apply_min_duration(
        speech, frame_samples=320, sample_rate=SR, min_speech_sec=0.3
    )
    assert out[20:50].all()
    assert not out[:20].any()
    assert not out[50:].any()


def test_apply_min_duration_drops_short_keeps_long_in_mixed():
    """混合：短爆音被丟、長段保留。"""
    speech = np.zeros(100, dtype=bool)
    speech[10] = True       # 爆音 → 丟
    speech[30:60] = True    # 600ms → 留
    speech[80] = True       # 爆音 → 丟
    out = vad_gate.apply_min_duration(
        speech, frame_samples=320, sample_rate=SR, min_speech_sec=0.3
    )
    assert not out[10]
    assert out[30:60].all()
    assert not out[80]


# --- apply_pad：每段前後擴張，避免切掉氣口 ---


def test_apply_pad_extends_segment_both_sides():
    """單一 segment 前後各擴 pad_sec。"""
    speech = np.zeros(200, dtype=bool)
    speech[100:120] = True  # 中段
    # pad = 100ms / frame = 20ms → 擴 5 frames 兩側
    out = vad_gate.apply_pad(
        speech, frame_samples=320, sample_rate=SR, pad_sec=0.1
    )
    assert out[95:125].all()
    assert not out[:95].any()
    assert not out[125:].any()


def test_apply_pad_clamps_at_edges():
    """段落貼著頭尾 → pad 不能跑出 array 範圍。"""
    speech = np.zeros(50, dtype=bool)
    speech[0:5] = True
    speech[45:50] = True
    out = vad_gate.apply_pad(
        speech, frame_samples=320, sample_rate=SR, pad_sec=0.1
    )
    assert out[0:5].all()
    assert out[45:50].all()
    assert len(out) == 50  # 沒有意外增長


# --- gate_samples：pipeline 整合 ---


def test_gate_samples_silence_in_silence_out():
    """純靜音 → 整段保持靜音。"""
    samples = _silence(1.0)
    out = vad_gate.gate_samples(
        samples, SR,
        threshold=0.02, min_speech_sec=0.3, pad_sec=0.15,
    )
    assert out.dtype == np.int16
    assert len(out) == len(samples)
    assert (out == 0).all()


def test_gate_samples_long_speech_passes_through():
    """長段說話訊號 → 大部分原樣保留（含 pad）。"""
    samples = np.concatenate([
        _silence(0.5),
        _tone(1.0, amp=16000),  # 1 秒 > 0.3s min_speech
        _silence(0.5),
    ])
    out = vad_gate.gate_samples(
        samples, SR,
        threshold=0.02, min_speech_sec=0.3, pad_sec=0.15,
    )
    # 中段（含 pad）保留 → 非零樣本數應接近 1 秒 + 0.3 秒 pad
    nonzero_ratio = (out != 0).sum() / len(out)
    assert 0.4 < nonzero_ratio < 0.7


def test_gate_samples_short_burst_gets_silenced():
    """50ms 短爆音（< 300ms min_speech）→ 被閘掉。"""
    samples = np.concatenate([
        _silence(0.5),
        _tone(0.05, amp=16000),  # 50ms 爆音
        _silence(0.5),
    ])
    out = vad_gate.gate_samples(
        samples, SR,
        threshold=0.02, min_speech_sec=0.3, pad_sec=0.15,
    )
    assert (out == 0).all()


def test_gate_samples_preserves_length_and_dtype():
    """輸出長度 / dtype 必須與輸入一致（下游 ffmpeg 寫 wav 才不會錯時長）。"""
    samples = _tone(2.0, amp=16000)
    out = vad_gate.gate_samples(
        samples, SR,
        threshold=0.02, min_speech_sec=0.3, pad_sec=0.15,
    )
    assert len(out) == len(samples)
    assert out.dtype == np.int16
