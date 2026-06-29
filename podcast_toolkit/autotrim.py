"""自動去頭去尾：偵測正片頭尾靜音，補進 episode.yaml 的 head/tail_trim_sec。

設計取捨：
- **只補「沒設過」的值**（current <= 0）。使用者手動標好的 trim 一律尊重、不覆寫
  （除非 force=True 明確要求重測）。trim 是給人最後在 UI 微調的「建議起點」。
- 用既有的 silencedetect（ffmpeg -af silencedetect）；尾段靜音 = 一路靜音到檔尾的長度，
  正好對應 segment_plan 砍掉 (main_dur - tail_trim, main_dur) 的語意。
- 寫回走 safe_load → 改 key → safe_dump(sort_keys=False)，保留 deletions 等其他欄位。

只動 episode.yaml 的兩個數字，不碰字幕、不碰影片。偵測不到靜音就不寫（回空 dict）。
"""
from __future__ import annotations

import yaml

from podcast_toolkit.episode import Episode
from podcast_toolkit.silencedetect import detect_head_silence, detect_tail_silence

# 靜音短於此值（秒）不值得標 trim（避免把零點幾秒的氣口當成要砍的頭尾）
_MIN_TRIM_SEC = 1.0


def run(ep: Episode, *, force: bool = False, progress=None) -> dict:
    """偵測頭尾靜音、補進 episode.yaml。回 {欄位: 新值} 的實際改動（沒改回空 dict）。

    progress(msg: str) 可選，回報目前在跑哪一步。
    """
    video = ep.main_video()
    if not video.exists():
        if progress:
            progress(f"找不到正片 {video.name}，略過去頭尾")
        return {}

    yaml_path = ep.dir / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    cur_head = float(data.get("head_trim_sec") or 0)
    cur_tail = float(data.get("tail_trim_sec") or 0)

    changes: dict[str, float] = {}

    if force or cur_head <= 0:
        if progress:
            progress("偵測開頭靜音…")
        head = detect_head_silence(video)
        if head >= _MIN_TRIM_SEC and abs(head - cur_head) > 0.05:
            changes["head_trim_sec"] = round(head, 2)

    if force or cur_tail <= 0:
        if progress:
            progress("偵測結尾靜音…")
        tail = detect_tail_silence(video)
        if tail >= _MIN_TRIM_SEC and abs(tail - cur_tail) > 0.05:
            changes["tail_trim_sec"] = round(tail, 2)

    if changes:
        data.update(changes)
        yaml_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    return changes
