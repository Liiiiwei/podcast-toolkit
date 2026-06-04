"""Dashboard 純函式：episode stage / recent / list_episodes。

不依賴 FastAPI，方便單元測試。
"""
from __future__ import annotations
from pathlib import Path

from podcast_toolkit.episode import Episode


def episode_stage(ep_dir: Path) -> str:
    """回傳集數階段：broken / empty / needs_transcribe / needs_assemble / done。"""
    try:
        ep = Episode(ep_dir)
    except Exception:
        return "broken"
    if not ep.main_video().exists():
        return "empty"
    if not ep.output_v2_srt().exists():
        return "needs_transcribe"
    if not (ep.output_yt_video().exists() or ep.output_reels_video().exists()):
        return "needs_assemble"
    return "done"
