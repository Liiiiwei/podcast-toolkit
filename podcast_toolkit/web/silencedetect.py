"""跑 ffmpeg silencedetect filter，估開頭靜音長度（給 UI 智慧建議 trim head 用）。"""
from __future__ import annotations
import subprocess
from pathlib import Path


# 開頭靜音容忍偏移：silence_start 在這個值以內都算是「從頭開始的靜音」
_HEAD_TOLERANCE_SEC = 0.1


def parse_head_silence(stderr: str) -> float:
    """從 ffmpeg silencedetect 的 stderr 抓開頭靜音長度（秒）。
    若開頭非靜音 → 回 0.0。"""
    in_head = False
    for line in stderr.splitlines():
        if "silence_start:" in line:
            try:
                start = float(line.split("silence_start:")[1].strip().split()[0])
            except (ValueError, IndexError):
                continue
            if not in_head:
                if start < _HEAD_TOLERANCE_SEC:
                    in_head = True
                else:
                    return 0.0
        elif "silence_end:" in line and in_head:
            try:
                end_str = line.split("silence_end:")[1].strip().split("|")[0].strip()
                end = float(end_str.split()[0])
            except (ValueError, IndexError):
                continue
            return end
    return 0.0


def detect_head_silence(
    media_path: Path,
    *,
    threshold_db: float = -30,
    min_dur: float = 0.5,
    timeout: float = 120.0,
) -> float:
    """跑 ffmpeg silencedetect 找開頭靜音長度（秒）。回 0 表示開頭非靜音。"""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(media_path),
        "-af",
        f"silencedetect=noise={threshold_db}dB:d={min_dur}",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"找不到 ffmpeg：{e}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg silencedetect 超時（>{timeout}s）")
    return parse_head_silence(result.stderr)
