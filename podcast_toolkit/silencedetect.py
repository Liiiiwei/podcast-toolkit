"""跑 ffmpeg silencedetect filter，估開頭 / 結尾靜音長度（給 UI 智慧建議 trim 用）。"""
from __future__ import annotations
import re
import subprocess
from pathlib import Path


# 開頭靜音容忍偏移：silence_start 在這個值以內都算是「從頭開始的靜音」
_HEAD_TOLERANCE_SEC = 0.1
# 結尾靜音容忍偏移：silence_end 落在 (總長 - 此值) 之後就算「一路靜音到檔尾」
_TAIL_TOLERANCE_SEC = 0.35

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


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


def parse_duration(stderr: str) -> float:
    """從 ffmpeg stderr 的 `Duration: HH:MM:SS.ss` 抓總長度（秒）。抓不到回 0.0。"""
    m = _DURATION_RE.search(stderr)
    if not m:
        return 0.0
    h, mm, ss = m.group(1), m.group(2), m.group(3)
    return int(h) * 3600 + int(mm) * 60 + float(ss)


def parse_tail_silence(stderr: str, total_dur: float) -> float:
    """從 silencedetect stderr 抓「結尾一路靜音到檔尾」的長度（秒）。

    判斷：最後一段靜音若 (a) 有 silence_start 但沒對應 silence_end（靜音延續到 EOF），
    或 (b) silence_end 落在 (總長 - 容忍值) 之後 → 視為結尾靜音，長度 = 總長 - silence_start。
    結尾非靜音 → 回 0.0。total_dur 抓不到（<=0）也回 0.0（無法換算）。
    """
    if total_dur <= 0:
        return 0.0
    last_start = None          # 目前開著、還沒對應到 end 的 silence_start
    last_pair = None           # 最近一組完整 (start, end)
    for line in stderr.splitlines():
        if "silence_start:" in line:
            try:
                last_start = float(line.split("silence_start:")[1].strip().split()[0])
            except (ValueError, IndexError):
                continue
        elif "silence_end:" in line:
            try:
                end_str = line.split("silence_end:")[1].strip().split("|")[0].strip()
                end = float(end_str.split()[0])
            except (ValueError, IndexError):
                continue
            if last_start is not None:
                last_pair = (last_start, end)
                last_start = None
    # 有開著沒關的 silence_start → 靜音延續到檔尾
    if last_start is not None:
        return max(0.0, total_dur - last_start)
    # 最後一組靜音的 end 貼著檔尾 → 結尾靜音
    if last_pair is not None:
        start, end = last_pair
        if end >= total_dur - _TAIL_TOLERANCE_SEC:
            return max(0.0, total_dur - start)
    return 0.0


def _run_silencedetect(
    media_path: Path, *, threshold_db: float, min_dur: float, timeout: float
) -> str:
    """跑一次 silencedetect，回 stderr（head / tail 共用，只解一次碼）。

    `-vn` 關鍵：silencedetect 是 audio filter，但不加 -vn 的話 ffmpeg 仍會把整段
    視訊解碼丟 null（4K 長片要好幾分鐘的白工）。只解音訊 → 36 分片從數分鐘降到數十秒。
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-vn",
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
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"找不到 ffmpeg：{e}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg silencedetect 超時（>{timeout}s）")
    return result.stderr


def detect_head_silence(
    media_path: Path,
    *,
    threshold_db: float = -30,
    min_dur: float = 0.5,
    timeout: float = 120.0,
) -> float:
    """跑 ffmpeg silencedetect 找開頭靜音長度（秒）。回 0 表示開頭非靜音。"""
    stderr = _run_silencedetect(
        media_path, threshold_db=threshold_db, min_dur=min_dur, timeout=timeout
    )
    return parse_head_silence(stderr)


def detect_tail_silence(
    media_path: Path,
    *,
    threshold_db: float = -30,
    min_dur: float = 0.5,
    timeout: float = 600.0,
) -> float:
    """跑 ffmpeg silencedetect 找結尾靜音長度（秒）。回 0 表示結尾非靜音。

    結尾要解到檔尾才知道，timeout 預設給長一點（整片解碼，長集可能數十秒）。
    """
    stderr = _run_silencedetect(
        media_path, threshold_db=threshold_db, min_dur=min_dur, timeout=timeout
    )
    return parse_tail_silence(stderr, parse_duration(stderr))


def parse_silence_intervals(stderr: str) -> list[tuple[float, float]]:
    """從 silencedetect stderr 抓出**全部** (silence_start, silence_end) 配對（秒）。

    用於「全片去空拍」：要整片每一段靜音，而非只頭/尾。silence_start 沒對應到
    silence_end（靜音延續到 EOF）的開放區間直接丟棄（中段去空拍不處理片尾開放段，
    片尾留給 tail_trim）。filter 端已用 d={min_dur} 過濾，這裡拿到的都已 >= 門檻。
    """
    intervals: list[tuple[float, float]] = []
    cur_start: float | None = None
    for line in stderr.splitlines():
        if "silence_start:" in line:
            try:
                cur_start = float(line.split("silence_start:")[1].strip().split()[0])
            except (ValueError, IndexError):
                cur_start = None
        elif "silence_end:" in line and cur_start is not None:
            try:
                end_str = line.split("silence_end:")[1].strip().split("|")[0].strip()
                end = float(end_str.split()[0])
            except (ValueError, IndexError):
                cur_start = None
                continue
            if end > cur_start:
                intervals.append((cur_start, end))
            cur_start = None
    return intervals


def detect_silence_intervals(
    media_path: Path,
    *,
    threshold_db: float = -30.0,
    min_dur: float = 0.8,
    timeout: float = 900.0,
) -> list[tuple[float, float]]:
    """跑 ffmpeg silencedetect 找**整片所有**靜音區間（秒），給「全片去空拍」用。

    回 [(start, end), ...]（媒體自身時間軸）。只含 >= min_dur 的靜音（filter 端已過濾）。
    整片解碼較久（-vn 已只解音訊），timeout 給長一點。
    """
    stderr = _run_silencedetect(
        media_path, threshold_db=threshold_db, min_dur=min_dur, timeout=timeout
    )
    return parse_silence_intervals(stderr)
