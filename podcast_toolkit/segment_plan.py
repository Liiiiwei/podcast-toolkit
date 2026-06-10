"""雙鏡頭分段計畫：把字幕卡 + 鏡頭對應 + 刪除/trim 轉成連續區段表。

純函式，無副作用，方便獨立測試與下游 ffmpeg filter 組裝。

回傳格式：list of {"cam": "a"|"b", "start": float, "end": float}
- 時間是「原始正片」的 timeline（cam B 的 sync offset 由 ffmpeg filter 端處理）
- 已套用 carry-forward、扣除 deletions / head_trim / tail_trim
- 相鄰同 cam 區段自動合併
"""
from __future__ import annotations


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """sort + merge overlapping/adjacent intervals。"""
    if not intervals:
        return []
    intervals = sorted(intervals)
    out: list[tuple[float, float]] = []
    for a, b in intervals:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _cam_at(time: float, transitions: list[tuple[float, str]], default_cam: str) -> str:
    """二分查找：給定時間點返回該時刻有效的 cam。transitions 已依時間排序。"""
    cam = default_cam
    for t, c in transitions:
        if t <= time:
            cam = c
        else:
            break
    return cam


def build_segment_plan(
    cards: list[dict],
    deletions: list[int],
    cameras_mapping: dict[int, str],
    main_dur: float,
    head_trim_sec: float = 0.0,
    tail_trim_sec: float = 0.0,
    default_cam: str = "a",
) -> list[dict]:
    """組合字幕卡 + 刪除 + trim + 鏡頭對應 → 連續區段清單。

    Args:
        cards: srt 解析後的字幕卡（含 idx / start / end）
        deletions: 要刪掉的卡 idx
        cameras_mapping: 顯式 cam 對應（只含 explicit，沒標的會 carry-forward）
        main_dur: 正片總時長（秒）
        head_trim_sec: 開頭要砍掉的秒數
        tail_trim_sec: 結尾要砍掉的秒數
        default_cam: 沒任何標記前的預設 cam（通常 "a"）
    """
    deletions_set = {int(i) for i in (deletions or [])}

    # 依 idx 排序卡片，建 cam transition 時間軸（被刪的卡不參與切換）
    sorted_cards = sorted(cards, key=lambda c: c["idx"])
    transitions: list[tuple[float, str]] = []
    current_cam = default_cam
    for c in sorted_cards:
        if c["idx"] in deletions_set:
            continue
        explicit = cameras_mapping.get(int(c["idx"]))
        if explicit and explicit != current_cam:
            transitions.append((float(c["start"]), explicit))
            current_cam = explicit

    # 蒐集要跳過的時間區間（被刪卡片 + head/tail trim）
    by_idx = {c["idx"]: c for c in sorted_cards}
    skip_intervals: list[tuple[float, float]] = []
    for idx in deletions_set:
        c = by_idx.get(idx)
        if c is not None:
            skip_intervals.append((float(c["start"]), float(c["end"])))
    if head_trim_sec > 0:
        skip_intervals.append((0.0, head_trim_sec))
    if tail_trim_sec > 0:
        skip_intervals.append((max(0.0, main_dur - tail_trim_sec), main_dur))
    merged_skips = _merge_intervals(skip_intervals)

    # 取得 keep intervals（[0, main_dur] 扣掉所有 skip）
    keep: list[tuple[float, float]] = []
    prev = 0.0
    for a, b in merged_skips:
        if a > prev:
            keep.append((prev, a))
        prev = max(prev, b)
    if prev < main_dur:
        keep.append((prev, main_dur))

    # 在每個 keep 區間內，依 cam transition 切段
    raw_segments: list[dict] = []
    for ks, ke in keep:
        cuts = sorted(t for t, _ in transitions if ks < t < ke)
        boundaries = [ks] + cuts + [ke]
        for i in range(len(boundaries) - 1):
            seg_start, seg_end = boundaries[i], boundaries[i + 1]
            if seg_end <= seg_start:
                continue
            seg_cam = _cam_at(seg_start, transitions, default_cam)
            raw_segments.append({"cam": seg_cam, "start": seg_start, "end": seg_end})

    # 合併相鄰同 cam 段（包含跨 keep 區間的：若 deletion 中間切斷則不合併）
    merged: list[dict] = []
    for s in raw_segments:
        if (
            merged
            and merged[-1]["cam"] == s["cam"]
            and abs(merged[-1]["end"] - s["start"]) < 1e-6
        ):
            merged[-1]["end"] = s["end"]
        else:
            merged.append(dict(s))

    return merged
