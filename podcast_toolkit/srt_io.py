"""SRT 解析與序列化。共用給 web/episode_io.py。"""
from __future__ import annotations
from typing import Iterable


# 中文 podcast 對話約 3-5 字/秒；用 0.3s/字當「合理語速」上界。
# 切卡時若原卡 dur 比 sum(chars)*RATE 還大（trailing silence），
# sub-cards 從 t0 緊湊排，尾段不指派字幕；避免 sub-card 1 被推進靜音裡。
SPLIT_SEC_PER_CHAR = 0.3


def allocate_split_times(
    t0: float, t1: float, parts: list[str]
) -> list[tuple[float, float]]:
    """把 [t0, t1] 依 parts 字數切成 N 段時間。

    若原卡夠長能容納「字數 × 合理語速」→ 從 t0 緊湊排，剩餘 trailing silence 不分配；
    若原卡比語速 budget 還短 → 退回比例分配，貼滿整段。
    """
    lengths = [max(len(p), 1) for p in parts]
    total = sum(lengths)
    dur = t1 - t0
    budget = total * SPLIT_SEC_PER_CHAR
    rate = SPLIT_SEC_PER_CHAR if budget <= dur else dur / total
    out: list[tuple[float, float]] = []
    cum = 0.0
    for ln in lengths:
        start = t0 + cum
        cum += ln * rate
        end = min(t0 + cum, t1)
        out.append((start, end))
    return out


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
            times = allocate_split_times(c["start"], c["end"], parts)
            for i, (part, (p_start, p_end)) in enumerate(zip(parts, times)):
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
