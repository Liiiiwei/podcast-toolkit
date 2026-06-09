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


def serialize(
    cards: Iterable[dict],
    overrides: dict[int, str] | None = None,
    splits: dict[int, list[str]] | None = None,
) -> str:
    """把 cards 寫回 srt 字串。

    overrides[idx]：覆寫對應 card 的文字（在 split 之前先 apply）
    splits[idx]：把該卡切成 N 段文字，時間依文字長度比例分配；
                 切完所有 idx 一律重編序號。
    """
    text, _ = serialize_with_map(cards, overrides=overrides, splits=splits)
    return text


def serialize_with_map(
    cards: Iterable[dict],
    overrides: dict[int, str] | None = None,
    splits: dict[int, list[str]] | None = None,
) -> tuple[str, list[tuple[int, int]]]:
    """同 serialize，但額外回傳 idx_map：
    new_idx (1-based) → (original_idx, part_idx)
    part_idx：未切的卡固定 0；切的卡 0..N-1。
    給 caller 翻譯 cameras_mapping / deletions / textOverrides 用。
    """
    overrides = overrides or {}
    splits = splits or {}
    out: list[str] = []
    idx_map: list[tuple[int, int]] = []
    new_idx = 1
    for c in cards:
        oid = c["idx"]
        base_text = overrides.get(oid, c["text"])
        parts = splits.get(oid)
        if parts and len(parts) > 1:
            lengths = [max(len(p), 1) for p in parts]
            total = sum(lengths)
            t0 = c["start"]
            dur = c["end"] - c["start"]
            cum = 0
            for i, part in enumerate(parts):
                p_start = t0 + dur * cum / total
                cum += lengths[i]
                p_end = t0 + dur * cum / total
                out.append(
                    f"{new_idx}\n{_s2ts(p_start)} --> {_s2ts(p_end)}\n{part}\n"
                )
                idx_map.append((oid, i))
                new_idx += 1
        else:
            out.append(
                f"{new_idx}\n{_s2ts(c['start'])} --> {_s2ts(c['end'])}\n{base_text}\n"
            )
            idx_map.append((oid, 0))
            new_idx += 1
    return "\n".join(out), idx_map
