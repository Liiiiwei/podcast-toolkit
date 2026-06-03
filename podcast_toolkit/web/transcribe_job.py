"""背景跑 Grok STT pipeline + resegment，餵狀態給前端 poll。

模組層級單一 job slot：同時間只能跑一個轉字幕。
前端流程：POST /api/transcribe → start_job() → 每秒 GET /api/transcribe/status。

Phase 順序：compress → upload → resegment → done
"""
from __future__ import annotations

import threading
from pathlib import Path
from time import monotonic
from typing import Any

from podcast_toolkit.episode import Episode
from podcast_toolkit.web import transcribe as _transcribe


_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "state": "idle",       # idle | running | done | error
    "phase": None,         # None | compress | upload | resegment
    "percent": 0.0,        # 該 phase 內 0-100
    "src_path": None,      # 來源檔（相對 ep.dir）
    "out_srt": None,       # _v2.srt 相對 ep.dir，成功才會塞
    "error": None,
    "started_at": None,
}


def get_status() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def _reset(**kwargs) -> None:
    with _LOCK:
        _STATE.update({
            "state": "idle",
            "phase": None,
            "percent": 0.0,
            "src_path": None,
            "out_srt": None,
            "error": None,
            "started_at": None,
        })
        _STATE.update(kwargs)


def _set(**kwargs) -> None:
    with _LOCK:
        _STATE.update(kwargs)


def start_job(
    ep: Episode, *, src_rel: str, provider: str, api_key: str
) -> dict[str, Any]:
    """開新 job；src_rel 是相對 ep.dir 的檔案路徑。provider: "xai" | "gemini"。"""
    with _LOCK:
        if _STATE["state"] == "running":
            raise RuntimeError("已有轉字幕正在進行中")

    src = (ep.dir / src_rel).resolve()
    # 防路徑跳脫
    src.relative_to(ep.dir)
    if not src.is_file():
        raise FileNotFoundError(f"找不到檔案：{src_rel}")

    _reset(
        state="running",
        phase="compress",
        percent=0.0,
        src_path=src_rel,
        started_at=monotonic(),
    )

    worker = threading.Thread(
        target=_run, args=(ep, src, provider, api_key), daemon=True
    )
    worker.start()
    return {"src_path": src_rel}


def _run(ep: Episode, src: Path, provider: str, api_key: str) -> None:
    """背景 worker：跑 pipeline → resegment → done / error。"""
    def progress(phase: str, percent: float) -> None:
        _set(phase=phase, percent=float(percent))

    try:
        _transcribe.run_pipeline(
            provider=provider,
            api_key=api_key,
            src_audio=src,
            out_srt=ep.main_srt(),
            work_dir=ep.subdir("work"),
            progress=progress,
        )
    except _transcribe.TranscribeError as e:
        _set(state="error", error=str(e))
        return
    except Exception as e:  # 其他預期外狀況也記下，避免 thread 沉默
        _set(state="error", error=f"轉字幕失敗：{e}")
        return

    # resegment：把字層 → 句子層 _v2.srt
    _set(phase="resegment", percent=0.0)
    try:
        from podcast_toolkit import resegment
        rc = resegment.run(ep.dir, force=True)
    except Exception as e:
        _set(state="error", error=f"resegment 失敗：{e}")
        return

    if rc != 0:
        _set(state="error", error=f"resegment 失敗 (rc={rc})")
        return

    out_srt_rel = str(ep.output_v2_srt().relative_to(ep.dir))
    _set(state="done", phase="resegment", percent=100.0, out_srt=out_srt_rel)
