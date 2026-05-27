"""SRT 解析與序列化。共用給 web/episode_io.py。"""
from __future__ import annotations
from typing import Iterable


def _ts2s(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _s2ts(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse(text: str) -> list[dict]:
    """解析 srt 字串 → list of {idx, start, end, text}。idx 為 srt 原本的 1-based 序號。"""
    cards: list[dict] = []
    blocks = [b for b in text.replace("\r\n", "\n").strip().split("\n\n") if b.strip()]
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        start_str, _, end_str = lines[1].partition(" --> ")
        cards.append(
            {
                "idx": idx,
                "start": _ts2s(start_str.strip()),
                "end": _ts2s(end_str.strip()),
                "text": "\n".join(lines[2:]),
            }
        )
    return cards


def serialize(cards: Iterable[dict], overrides: dict[int, str] | None = None) -> str:
    """把 cards 寫回 srt 字串。overrides[idx] 會覆寫對應 card 的文字。"""
    overrides = overrides or {}
    out: list[str] = []
    for c in cards:
        text = overrides.get(c["idx"], c["text"])
        out.append(
            f"{c['idx']}\n{_s2ts(c['start'])} --> {_s2ts(c['end'])}\n{text}\n"
        )
    return "\n".join(out)
