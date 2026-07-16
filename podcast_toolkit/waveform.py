"""字幕卡時間軸波形（Option B：後端算 peaks + 靜音，落 sidecar 快取）。

編輯器時間軸原本只有色塊，難判斷每張卡的真實開始/結束點。這裡用 ffmpeg 把音訊
解碼成單聲道 PCM，切成固定時長的「桶」取每桶峰值 → 一維 peaks 陣列，前端畫成波形；
再附上整片靜音區間，供「拖曳吸附靜音」用。

設計硬條件（使用者核准）：
  · 不影響檔案解析：只讀來源音訊，產物寫進 04_工作檔/ sidecar，不碰 srt / 母帶。
  · 前端不卡：重活（解碼、算峰值、偵測靜音）全在後端一次算完並快取；前端只收壓縮後的
    小陣列畫一次。快取以來源檔簽章（mtime+size）驗證，換檔自動失效重算。
"""
from __future__ import annotations

import audioop
import json
import subprocess
from pathlib import Path

from podcast_toolkit import silencedetect
from podcast_toolkit.assemble import ffmpeg_bin

# 解碼取樣率（單聲道）：波形只看振幅輪廓，8k 已足夠且解碼快、記憶體省（約真實取樣的 1/6）
_SR = 8000
# 每桶時長（毫秒）→ 波形一根柱；20ms ≈ 50 柱/秒，縮到最大也夠細
_BUCKET_MS = 20
# peak 正規化上限：0.._PEAK_MAX（前端當百分比高度用）
_PEAK_MAX = 100
# 靜音偵測門檻（吸附用）：比「去空拍」的 0.8s 短，才抓得到氣口/換氣的小停頓
_SNAP_MIN_SILENCE = 0.3
_SNAP_THRESHOLD_DB = -30.0
# 快取格式版本：改動 peaks 演算法/欄位時 +1，讓舊快取自動失效
_CACHE_VERSION = 1


def _src_signature(src: Path) -> tuple[int, int]:
    """來源檔簽章 (mtime 取整秒, size)——換片/重新匯出就會變 → 快取失效重算。"""
    st = src.stat()
    return int(st.st_mtime), st.st_size


def compute_peaks(pcm: bytes, *, sr: int = _SR, bucket_ms: int = _BUCKET_MS) -> list[int]:
    """s16le 單聲道 PCM → 每桶峰值（0.._PEAK_MAX 整數）。純函式。

    每桶取該桶內樣本絕對值最大者（audioop.max，C 速度），正規化到 0.._PEAK_MAX。
    最後不足一桶的殘尾照收（成品時長多半非整桶）；空輸入 → 回空清單。
    """
    bucket_bytes = max(1, int(sr * bucket_ms / 1000)) * 2  # 2 bytes/sample（s16）
    peaks: list[int] = []
    for i in range(0, len(pcm), bucket_bytes):
        chunk = pcm[i:i + bucket_bytes]
        if len(chunk) % 2:                 # 防殘半個樣本讓 audioop 拋錯
            chunk = chunk[:-1]
        if not chunk:
            break
        amp = audioop.max(chunk, 2)        # 0..32768（絕對值最大樣本）
        peaks.append(min(_PEAK_MAX, round(amp / 32768 * _PEAK_MAX)))
    return peaks


def _decode_pcm(target: Path, *, sr: int = _SR, timeout: float = 900.0) -> bytes:
    """ffmpeg 把 target 解成單聲道 s16le PCM（-vn 只解音訊，4K 長片也快）。"""
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-nostats", "-vn",
        "-i", str(target),
        "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError as e:
        raise RuntimeError(f"找不到 ffmpeg：{e}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg 解碼波形超時（>{timeout}s）")
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"ffmpeg 解碼波形失敗（rc={proc.returncode}）：{tail[0]}")
    return proc.stdout


def _read_cache(cache: Path, sig: tuple[int, int], src_name: str) -> dict | None:
    """讀 sidecar 快取；版本/來源簽章不符（含換檔）→ 回 None 要求重算。"""
    if not cache.exists():
        return None
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (data.get("version") == _CACHE_VERSION
            and data.get("src") == src_name
            and data.get("src_mtime") == sig[0]
            and data.get("src_size") == sig[1]):
        return data
    return None


def build_waveform(target: Path, cache: Path) -> dict:
    """算/讀 target 的波形資料（peaks + 靜音 + 時長），落 cache 後回傳。

    先看 cache 是否對得上來源簽章（命中直接回，秒級）；否則解碼算峰值 + 偵測靜音，
    寫進 cache。回傳 dict 的 peaks 為 0.._PEAK_MAX 整數陣列，silences 為 [[start,end],…]。
    """
    sig = _src_signature(target)
    hit = _read_cache(cache, sig, target.name)
    if hit is not None:
        return hit

    pcm = _decode_pcm(target)
    peaks = compute_peaks(pcm)
    duration = round(len(peaks) * _BUCKET_MS / 1000.0, 3)
    silences = [
        [round(s, 3), round(e, 3)]
        for s, e in silencedetect.detect_silence_intervals(
            target, threshold_db=_SNAP_THRESHOLD_DB, min_dur=_SNAP_MIN_SILENCE
        )
    ]
    data = {
        "version": _CACHE_VERSION,
        "src": target.name,
        "src_mtime": sig[0],
        "src_size": sig[1],
        "sr": _SR,
        "bucket_ms": _BUCKET_MS,
        "peak_max": _PEAK_MAX,
        "duration": duration,
        "peaks": peaks,
        "silences": silences,
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data
