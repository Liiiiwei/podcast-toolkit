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


def save_state(ep: Episode, payload: dict[str, Any]) -> None:
    """把前端 payload 寫回：episode.yaml 的 crop / deletions、覆寫 _v2.srt。"""
    yaml_path = ep.dir / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    # crop
    crop = payload.get("crop")
    if crop:
        data["crop"] = {
            "x": float(crop["x"]),
            "y": float(crop["y"]),
            "width": float(crop["width"]),
            "height": float(crop["height"]),
        }
    else:
        data.pop("crop", None)

    # deletions
    deletions = list(payload.get("deletions") or [])
    if deletions:
        data["deletions"] = [int(i) for i in deletions]
    else:
        data.pop("deletions", None)

    yaml_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    # _v2.srt 覆寫前先留一份滾動備份，避免誤存後找不回原稿
    v2 = ep.output_v2_srt()
    original = v2.read_text(encoding="utf-8")
    backup = v2.with_suffix(v2.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")

    cards = srt_io.parse(original)
    overrides = {
        int(c["idx"]): c["text"]
        for c in (payload.get("cards") or [])
        if c.get("text")
    }
    v2.write_text(srt_io.serialize(cards, overrides=overrides), encoding="utf-8")
