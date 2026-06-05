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
    """用 FFT 互相關找 cam B 相對 cam A 的秒偏移。

    正值 = cam B 比 cam A 晚開始；負值 = cam B 比 cam A 早開始。

    O(N log N) FFT 實作；對 120 秒 16 kHz 兩段（≈ 192 萬樣本），
    比 np.correlate(mode="full") 的 O(N²) 快約 100x：2-5 分鐘 → < 2 秒。
    內部先做 zero-mean 避免 DC 分量主導 correlation。
    """
    a = audio_a.astype(np.float64)
    b = audio_b.astype(np.float64)
    # 去 DC：避免靜音段的非零基線主導內積
    a = a - a.mean()
    b = b - b.mean()

    m = len(a)
    n = len(b)
    # FFT 長度取 >= m+n-1 的下一個 2 的冪（FFT 對 2 的冪最佳）
    n_fft = 1 << (m + n - 2).bit_length()

    # 對實數信號：np.correlate(a, b)[k] = IFFT(FFT(a) * conj(FFT(b)))[k]
    a_fft = np.fft.rfft(a, n_fft)
    b_fft = np.fft.rfft(b, n_fft)
    corr_circular = np.fft.irfft(a_fft * np.conj(b_fft), n_fft)

    # FFT 輸出是 circular 順序：index 0..m-1 = 正向 lag、index n_fft-(n-1)..n_fft-1 = 負向 lag。
    # 重排成 np.correlate(a, b, mode="full") 的線性順序 [lag = -(n-1) ... lag = m-1]，
    # 與舊版 peak_index → lag_samples 換算保持一致。
    corr = np.concatenate([corr_circular[n_fft - (n - 1):], corr_circular[:m]])
    peak_index = int(np.argmax(corr))
    lag_samples = (n - 1) - peak_index
    return lag_samples / float(sample_rate)


def compute_manual_offset(events: list[dict]) -> tuple[float, list[float]]:
    """T23c：使用者手動標的三組 (a, b) 時間點 → 算 offset + 一致性 deltas。

    events 必須剛好 3 筆，每筆 {"a": float, "b": float}。
    a = 在 cam A 上聽到第 i 個事件的時間（秒）
    b = 在 cam B 上聽到同一個事件的時間（秒）

    offset = mean(a[i] - b[i])  → 與 T23b 一致：正值 = cam B 比 cam A 晚開始
    deltas[i] = (a[i] - b[i]) - offset → 三筆的離差，幫使用者看一致性

    錯誤：
    - 數量不是 3 → ValueError
    - a / b 不是數字 → ValueError
    """
    if not isinstance(events, list) or len(events) != 3:
        raise ValueError("需要剛好三筆事件")
    diffs: list[float] = []
    for ev in events:
        if not isinstance(ev, dict):
            raise ValueError("每筆事件必須是 {a, b} 物件")
        a = ev.get("a")
        b = ev.get("b")
        if not isinstance(a, (int, float)) or isinstance(a, bool):
            raise ValueError(f"事件 a 必須是數字：{a!r}")
        if not isinstance(b, (int, float)) or isinstance(b, bool):
            raise ValueError(f"事件 b 必須是數字：{b!r}")
        diffs.append(float(a) - float(b))
    offset = sum(diffs) / 3.0
    deltas = [d - offset for d in diffs]
    return offset, deltas


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
