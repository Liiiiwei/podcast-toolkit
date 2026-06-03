"""T23b 音訊互相關自動對齊：純函式單元測試。

不跑 ffmpeg 端對端（會慢且需真檔），只測：
- compute_lag_seconds 在已知訊號上能回收正確位移
- extract_audio_pcm 對不存在的檔案 raise RuntimeError
"""
from pathlib import Path

import numpy as np
import pytest

from podcast_toolkit import audio_align


SAMPLE_RATE = 16000


def _sin_wave(freq_hz: float, duration_sec: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """造一段給定頻率的正弦波（int16 振幅，避免後續 zero-mean 失真）。"""
    t = np.arange(int(duration_sec * sample_rate)) / sample_rate
    return (np.sin(2 * np.pi * freq_hz * t) * 16000).astype(np.int16)


def test_compute_lag_seconds_returns_zero_for_identical_signals():
    # 兩段一樣的訊號 → lag 應為 0
    audio = _sin_wave(440.0, 1.0)
    lag = audio_align.compute_lag_seconds(audio, audio, sample_rate=SAMPLE_RATE)
    # 完全相同 → 容忍 ±1 sample
    assert abs(lag) <= 1.0 / SAMPLE_RATE


def test_compute_lag_seconds_recovers_known_positive_shift():
    # cam B 比 cam A 晚 0.5 秒：B 前面補 0.5 秒靜音，內容才開始
    # → lag 應 ≈ +0.5 秒（cam B 晚開始為正）
    base = _sin_wave(440.0, 1.0)
    shift_samples = int(0.5 * SAMPLE_RATE)
    cam_a = base
    cam_b = np.concatenate([np.zeros(shift_samples, dtype=np.int16), base])

    lag = audio_align.compute_lag_seconds(cam_a, cam_b, sample_rate=SAMPLE_RATE)
    # 容忍 ±2 samples → ±0.000125 秒
    assert lag == pytest.approx(0.5, abs=2.0 / SAMPLE_RATE)


def test_compute_lag_seconds_recovers_known_negative_shift():
    # cam B 比 cam A 早 0.3 秒：A 前面補 0.3 秒靜音
    # → lag 應 ≈ -0.3 秒（cam B 較早開始為負）
    base = _sin_wave(440.0, 1.0)
    shift_samples = int(0.3 * SAMPLE_RATE)
    cam_a = np.concatenate([np.zeros(shift_samples, dtype=np.int16), base])
    cam_b = base

    lag = audio_align.compute_lag_seconds(cam_a, cam_b, sample_rate=SAMPLE_RATE)
    assert lag == pytest.approx(-0.3, abs=2.0 / SAMPLE_RATE)


def test_extract_audio_pcm_raises_on_missing_file(tmp_path: Path):
    missing = tmp_path / "does_not_exist.mp4"
    with pytest.raises(RuntimeError, match="音訊抽取失敗"):
        audio_align.extract_audio_pcm(missing, duration_sec=1.0)
