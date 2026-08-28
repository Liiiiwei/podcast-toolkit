"""雙鏡頭分段計畫：把字幕卡 + 鏡頭對應 + 刪除/trim 轉成連續區段表。

純函式，無副作用，方便獨立測試與下游 ffmpeg filter 組裝。

回傳格式：list of {"cam": "a"|"b", "start": float, "end": float}
- 時間是「原始正片」的 timeline（cam B 的 sync offset 由 ffmpeg filter 端處理）
- 已套用 carry-forward、扣除 deletions / head_trim / tail_trim
- 相鄰同 cam 區段自動合併
"""
from __future__ import annotations


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """把重疊／相鄰的區間併成不重疊的聯集（依 start 排序後掃描）。

    必須在餵給 assemble._original_to_mp4_time 前先併：該函式是「逐段加總被刪長度」算位移，
    重疊區間會被各扣一次（母片 ffmpeg 的 select='not(A+B)' 是布林聯集只算一次，兩邊就會兜不攏）。
    1e-9 容差吸收浮點誤差，讓「頭尾相接」也算相鄰。
    """
    out: list[tuple[float, float]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + 1e-9:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def removed_with_trim(
    cut_intervals: list,
    main_dur: float,
    head_trim_sec: float = 0.0,
    tail_trim_sec: float = 0.0,
) -> list[tuple[float, float]]:
    """刪段區間 + 頭尾 trim → 這一集**全部被剪掉的時間**（已排序、不重疊）。

    併軌前 head/tail trim 在三處各自硬接（單機合成、Reels clip 換算、本檔 build_segment_plan），
    三份得同步維護才不會兜不攏；現在只有這一個產生點。

    Args:
        cut_intervals: 已位移到**正片/cam-A 時間軸**的刪除區間（呼叫端先做完 sync 位移）
        main_dur: 正片**原始**總時長（秒），tail trim 從這裡往回算
    """
    out: list[tuple[float, float]] = [
        (float(a), float(b)) for a, b in (cut_intervals or [])
    ]
    if head_trim_sec > 0:
        out.append((0.0, float(head_trim_sec)))
    if tail_trim_sec > 0:
        out.append((max(0.0, float(main_dur) - float(tail_trim_sec)), float(main_dur)))
    # 併重疊（trim 常與開頭/結尾的 cut／silence 重疊）→ 時間映射才不會重複扣同一段
    return merge_intervals(out)


def keep_intervals(removed: list, main_dur: float) -> list[tuple[float, float]]:
    """[0, main_dur] 扣掉 removed 之後剩下的保留區間（removed 不必先排序或去重）。

    單機與雙機共用這一份補集實作，「剪後時長」才會是同一個公式
    （前科：單機用「總長 − 刪除總長」相減、雙機用「保留段加總」相加，兩條公式各自維護）。
    """
    keep: list[tuple[float, float]] = []
    prev = 0.0
    for a, b in merge_intervals([(float(a), float(b)) for a, b in (removed or [])]):
        if a > prev:
            keep.append((prev, a))
        prev = max(prev, b)
    if prev < main_dur:
        keep.append((prev, main_dur))
    return keep


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
    cut_intervals: list,
    cam_transitions: list[dict],
    main_dur: float,
    head_trim_sec: float = 0.0,
    tail_trim_sec: float = 0.0,
    default_cam: str = "a",
) -> list[dict]:
    """組合刪段 + trim + 鏡頭切換點 → 連續區段清單（**純時間版，不需要字幕卡**）。

    Args:
        cut_intervals: **時間版**刪除區間 [(start, end), ...]（源/_v2 時間軸；與字幕脫鉤）
        cam_transitions: **時間版**鏡頭切換點 [{"t": 秒, "cam": "a"|"b"}]；
                         carry-forward：第一個切換前用 default_cam
        main_dur: 正片總時長（秒）
        head_trim_sec: 開頭要砍掉的秒數
        tail_trim_sec: 結尾要砍掉的秒數
        default_cam: 沒任何切換前的預設 cam（通常 "a"）
    """
    deleted_intervals: list[tuple[float, float]] = [
        (float(a), float(b)) for a, b in (cut_intervals or [])
    ]

    # 鏡頭切換點落在被刪區間內 → 無意義，丟掉
    def _in_deleted(t: float) -> bool:
        return any(a <= t < b for a, b in deleted_intervals)

    transitions: list[tuple[float, str]] = sorted(
        (float(tr["t"]), str(tr["cam"]))
        for tr in (cam_transitions or [])
        if not _in_deleted(float(tr["t"]))
    )

    # 要跳過的時間區間（刪除區間 + head/tail trim）→ keep 是它在 [0, main_dur] 內的補集。
    # 注意：上面的鏡頭切換點過濾只看 deleted_intervals，**不含 trim**——落在被 trim 掉的片頭裡
    # 的切換點仍要保留，否則第一個保留段會失去 carry-forward 的鏡頭狀態（退回 default_cam）。
    keep = keep_intervals(
        removed_with_trim(deleted_intervals, main_dur, head_trim_sec, tail_trim_sec),
        main_dur,
    )

    # 在每個 keep 區間內，依 cam transition 切段
    raw_segments: list[dict] = []
    for ks, ke in keep:
        cam_cuts = sorted(t for t, _ in transitions if ks < t < ke)
        boundaries = [ks] + cam_cuts + [ke]
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
