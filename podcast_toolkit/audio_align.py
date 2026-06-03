"""T23b 音訊互相關自動對齊。

用途：兩台攝影機（cam A / cam B）各自的影片檔，從前 N 秒抽 mono 16kHz PCM，
跑 numpy.correlate 找峰值，回推 cam B 相對 cam A 的秒偏移。

正值 = cam B 比 cam A 晚開始（cam B 落後）
負值 = cam B 比 cam A 早開始（cam B 領先）

不會寫 yaml，只回值 — 由前端拿去填 input、使用者按儲存再走 /api/save。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np


def extract_audio_pcm(
    video_path: Path,
    duration_sec: float = 120.0,
    sample_rate: int = 16000,
) -> np.ndarray:
    """用 ffmpeg subprocess 從 video_path 抽前 duration_sec 秒、mono、s16le PCM。

    回傳：np.int16 1D array（樣本數 ≈ duration_sec × sample_rate；若影片較短會自動截斷）。

    失敗：raise RuntimeError("音訊抽取失敗：...")
    """
    if not video_path.is_file():
        raise RuntimeError(f"音訊抽取失敗：找不到檔案 {video_path}")
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("音訊抽取失敗：找不到 ffmpeg")

    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss", "0",
        "-t", str(duration_sec),
        "-i", str(video_path),
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "s16le",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=False)
    except OSError as e:
        raise RuntimeError(f"音訊抽取失敗：subprocess 起不來 {e}")
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"音訊抽取失敗：ffmpeg 回傳 {proc.returncode}\n{stderr}")
    if not proc.stdout:
        raise RuntimeError("音訊抽取失敗：ffmpeg stdout 為空")
    return np.frombuffer(proc.stdout, dtype=np.int16)


def compute_lag_seconds(
    audio_a: np.ndarray,
    audio_b: np.ndarray,
    sample_rate: int = 16000,
) -> float:
    """用互相關找 cam B 相對 cam A 的秒偏移。

    正值 = cam B 比 cam A 晚開始；負值 = cam B 比 cam A 早開始。

    兩段 array 長度可以不一致 — numpy.correlate 會自動處理。
    內部先做 zero-mean 避免 DC 分量主導 correlation。
    """
    a = audio_a.astype(np.float64)
    b = audio_b.astype(np.float64)
    # 去 DC：避免靜音段的非零基線主導內積
    a = a - a.mean()
    b = b - b.mean()

    # full correlation：長度 = len(a) + len(b) - 1
    # numpy.correlate(a, b)[k] = sum_n a[n] * b[n - (k - (len(b)-1))]
    # → 峰值位置 k_peak 對應「把 b 向右平移 (k_peak - len(b) + 1)」後對齊 a
    # 若 b 在時間軸上「比 a 晚 d 秒」開始，我們要把 b 向左平移 d 才對齊 →
    # k_peak - (len(b) - 1) = -d_samples，亦即 d_samples = (len(b) - 1) - k_peak
    # 約定：正值 = cam B 比 cam A 晚開始
    corr = np.correlate(a, b, mode="full")
    peak_index = int(np.argmax(corr))
    lag_samples = (len(b) - 1) - peak_index
    return lag_samples / float(sample_rate)


def auto_align(
    cam_a_video: Path,
    cam_b_video: Path,
    duration_sec: float = 120.0,
) -> float:
    """編排：兩個影片各自抽 PCM → 算 lag → 回傳秒。

    供 /api/auto-align 呼叫。失敗會把 RuntimeError 直接往上拋。
    """
    audio_a = extract_audio_pcm(cam_a_video, duration_sec=duration_sec)
    audio_b = extract_audio_pcm(cam_b_video, duration_sec=duration_sec)
    return compute_lag_seconds(audio_a, audio_b)
