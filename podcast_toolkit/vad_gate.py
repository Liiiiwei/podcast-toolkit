"""VAD gate：把單路 mic 裡的串音壓成靜音，再餵給 Gemini。

設計理由：podcast 三路 mic 同時收音，每路會聽到別人的講話（串音）。
直接餵原檔給 Gemini → Gemini 會把所有人的話都轉成文字、speaker 對不上。
先做 RMS-based VAD 把「不是這個 mic 主講者」的段落壓成 0，Gemini 就只認得自己的話。

不用 webrtcvad / silero-vad 是因為：podcast 收音多半 dynamic mic + 近距，
主講 vs 串音的振幅差就有 20-30dB，純 RMS 已經夠用，省一個 ML 模型 dep。

Pipeline：
  detect_speech_frames → apply_min_duration → apply_pad → mask samples
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np


INT16_MAX = 32768.0


def detect_speech_frames(
    samples: np.ndarray,
    sample_rate: int,
    *,
    frame_ms: int = 20,
    threshold: float,
) -> np.ndarray:
    """切 frame_ms 一段，每段算 normalized RMS，>= threshold 視為說話。

    samples：int16 mono PCM
    threshold：0-1 normalized（int16 / 32768 後算 RMS）
    回傳：frame-level boolean array
    """
    frame_samples = int(sample_rate * frame_ms / 1000)
    n_frames = len(samples) // frame_samples
    if n_frames == 0:
        return np.zeros(0, dtype=bool)
    # 截掉尾巴不完整的 frame，避免 reshape 失敗
    usable = samples[: n_frames * frame_samples].astype(np.float32) / INT16_MAX
    frames = usable.reshape(n_frames, frame_samples)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    return rms >= threshold


def apply_min_duration(
    speech: np.ndarray,
    *,
    frame_samples: int,
    sample_rate: int,
    min_speech_sec: float,
) -> np.ndarray:
    """連續 True 段 < min_speech_sec 的壓成 False（過濾爆音 / 咳嗽 / 桌面碰撞）。"""
    if len(speech) == 0:
        return speech.copy()
    min_frames = max(1, int(min_speech_sec * sample_rate / frame_samples))
    out = speech.copy()
    # 找所有連續 True 段，計長度
    # 用 diff 找邊界：1 = False→True 起點，-1 = True→False 終點
    padded = np.concatenate([[False], speech, [False]])
    diff = np.diff(padded.astype(np.int8))
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0]
    for s, e in zip(starts, ends):
        if e - s < min_frames:
            out[s:e] = False
    return out


def apply_pad(
    speech: np.ndarray,
    *,
    frame_samples: int,
    sample_rate: int,
    pad_sec: float,
) -> np.ndarray:
    """每段 True 前後各擴 pad_sec，避免切掉氣口 / 子音起頭。

    用 segment-by-segment 擴張，相鄰段會自然 merge（True | True = True）。
    """
    if len(speech) == 0 or pad_sec <= 0:
        return speech.copy()
    pad_frames = int(pad_sec * sample_rate / frame_samples)
    if pad_frames == 0:
        return speech.copy()
    out = speech.copy()
    padded = np.concatenate([[False], speech, [False]])
    diff = np.diff(padded.astype(np.int8))
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0]
    n = len(speech)
    for s, e in zip(starts, ends):
        out[max(0, s - pad_frames) : min(n, e + pad_frames)] = True
    return out


def gate_samples(
    samples: np.ndarray,
    sample_rate: int,
    *,
    threshold: float,
    min_speech_sec: float,
    pad_sec: float,
    frame_ms: int = 20,
) -> np.ndarray:
    """完整 pipeline：frame-level VAD → 還原成 sample-level mask → 套用。

    保證輸出長度 / dtype 與輸入一致，下游 ffmpeg 寫 wav 才不會錯時長。
    """
    if len(samples) == 0:
        return samples.copy()
    frame_samples = int(sample_rate * frame_ms / 1000)
    speech = detect_speech_frames(samples, sample_rate, frame_ms=frame_ms, threshold=threshold)
    speech = apply_min_duration(
        speech, frame_samples=frame_samples, sample_rate=sample_rate, min_speech_sec=min_speech_sec
    )
    speech = apply_pad(
        speech, frame_samples=frame_samples, sample_rate=sample_rate, pad_sec=pad_sec
    )
    # 還原到 sample-level：每個 True frame 對應 frame_samples 個樣本
    sample_mask = np.repeat(speech, frame_samples)
    # 尾巴不完整 frame 一律壓成 0（沒判定過就保守處理）
    out = np.zeros(len(samples), dtype=np.int16)
    used = min(len(sample_mask), len(samples))
    out[:used] = np.where(sample_mask[:used], samples[:used], 0).astype(np.int16)
    return out


# --- ffmpeg I/O 層：讀任意格式 → s16le mono 16k → gate → 寫 wav ---


VAD_SAMPLE_RATE = 16000  # Gemini 不在意取樣率，固定 16k 省頻寬


def _read_pcm_mono(input_path: Path, sample_rate: int = VAD_SAMPLE_RATE) -> np.ndarray:
    """ffmpeg 讀任意音檔 → s16le mono 指定取樣率 → numpy int16。"""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("VAD gate 失敗：找不到 ffmpeg")
    cmd = [
        ffmpeg_bin, "-y", "-i", str(input_path),
        "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"VAD gate 失敗：ffmpeg 讀檔錯誤 {proc.returncode}\n{stderr}")
    return np.frombuffer(proc.stdout, dtype=np.int16)


def _write_wav(output_path: Path, samples: np.ndarray, sample_rate: int) -> None:
    """numpy int16 mono → ffmpeg pcm_s16le wav。
    用 ffmpeg 而不是 wave stdlib，是為了 codepath 一致（已知 ffmpeg 在）。
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("VAD gate 失敗：找不到 ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin, "-y",
        "-f", "s16le", "-ac", "1", "-ar", str(sample_rate),
        "-i", "-",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    proc = subprocess.run(cmd, input=samples.tobytes(), capture_output=True, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"VAD gate 失敗：ffmpeg 寫檔錯誤 {proc.returncode}\n{stderr}")


def gate_audio_file(
    input_path: Path,
    output_path: Path,
    *,
    threshold: float,
    min_speech_sec: float,
    pad_sec: float,
    sample_rate: int = VAD_SAMPLE_RATE,
) -> Path:
    """讀任意音檔 → VAD gate → 寫 16k mono wav 給 Gemini 用。"""
    samples = _read_pcm_mono(input_path, sample_rate=sample_rate)
    gated = gate_samples(
        samples, sample_rate,
        threshold=threshold, min_speech_sec=min_speech_sec, pad_sec=pad_sec,
    )
    _write_wav(output_path, gated, sample_rate)
    return output_path
