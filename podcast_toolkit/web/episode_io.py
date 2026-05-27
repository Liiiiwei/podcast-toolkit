"""把 Episode 物件 + _v2.srt 組成前端要的 JSON state，並負責寫回。"""
from __future__ import annotations
from pathlib import Path
from typing import Any

import yaml

from podcast_toolkit import srt_io
from podcast_toolkit.episode import Episode


def load_state(ep: Episode) -> dict[str, Any]:
    """讀 episode.yaml + _v2.srt → 給前端的初始狀態。"""
    v2 = ep.output_v2_srt()
    if not v2.exists():
        raise FileNotFoundError(f"找不到 _v2.srt：{v2}（請先跑 podcast resegment）")
    cards = srt_io.parse(v2.read_text(encoding="utf-8"))
    return {
        "name": ep.name,
        "crop": ep.cfg.get("crop"),
        "deletions": list(ep.cfg.get("deletions") or []),
        "cards": cards,
    }
