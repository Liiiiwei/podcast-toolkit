"""一鍵自動後製編排：把字幕選好後能自動做的事串起來，只剩人工確認 + 輸出。

步驟（任一步可關）：
1. **校對**（proofread）：本地 Claude Code / Gemini 逐句修字幕，就地改 _v2.srt（先備份）。
2. **鏡頭對應**（camera）：分軌集才有意義——要有 speakers.json（誰在講話）才推得出切鏡。
   單軌混音集沒有講者資料 → 自動略過，鏡頭維持原本（手動標記或無）。
3. **去頭去尾**（trim）：偵測正片頭尾靜音，補進 episode.yaml 的 head/tail_trim_sec
   （只補沒設過的，尊重手動值）。

設計原則同 proofread：對「非 Claude Code 使用者」零影響——校對 provider=auto 找不到引擎就
安靜跳過，鏡頭 / 去頭尾純本機 ffmpeg，不外聯任何 API。
"""
from __future__ import annotations

import sys
from pathlib import Path

from podcast_toolkit import autotrim, proofread
from podcast_toolkit.episode import Episode


def _run_camera(ep: Episode, *, force: bool = False) -> str:
    """鏡頭對應：需要 speakers.json(分軌 / Breeze 匯入才有)。沒有就略過(單軌集正常情況)。

    有講者資料 → 走 cameras_suggest(camera_rule:home 待著、feature 講者連講≥min_sec 才切),
    產出時間版 cameras.json v2(與字幕脫鉤,重斷句不破壞)。回一句人看的結果字串。
    """
    speakers = ep.output_v2_speakers_json()
    if not speakers.exists():
        return "略過（本集無分軌講者資料 speakers.json，鏡頭維持原設定）"
    from podcast_toolkit import cameras_suggest
    rc = cameras_suggest.run(ep, force=force)
    if rc == 0:
        return "已從 speakers.json 推出時間版鏡頭切換點"
    if rc == 1:
        return "已有 cameras.json，保留不動（--force 才重算）"
    return f"鏡頭建議未完成（rc={rc}）"


def run(
    episode_dir,
    *,
    do_proofread: bool = True,
    do_camera: bool = True,
    do_trim: bool = True,
    provider: str | None = None,
    model: str | None = None,
    force: bool = False,
    progress=None,
) -> int:
    """跑自動後製管線。回 exit code（0 成功；3 缺字幕；其餘沿用各步）。"""
    ep = Episode(Path(episode_dir))
    summary: list[tuple[str, str]] = []

    def banner(msg: str) -> None:
        print(f"\n▶ {msg}", flush=True)
        if progress:
            progress(msg)

    def chunk_log(pct: float) -> None:
        print(f"  校對 {pct:.0f}%", flush=True)
        if progress:
            progress(f"校對 {pct:.0f}%")

    # 1. 校對
    if do_proofread:
        banner("字幕校對")
        rc = proofread.run(
            ep.dir, provider=provider, model=model, force=force, progress=chunk_log,
        )
        if rc == 3:  # 沒字幕 → 整條管線沒東西可做，直接結束
            print("✗ 沒有 _v2.srt，自動管線中止。", file=sys.stderr)
            return 3
        summary.append(("字幕校對", "完成" if rc == 0 else f"rc={rc}"))

    # 2. 鏡頭對應
    if do_camera:
        banner("鏡頭對應")
        result = _run_camera(ep, force=force)
        print(f"  {result}")
        summary.append(("鏡頭對應", result))

    # 3. 去頭去尾
    if do_trim:
        banner("去頭去尾")
        changes = autotrim.run(ep, force=force, progress=lambda m: print(f"  {m}"))
        if changes:
            parts = []
            for k, v in changes.items():
                parts.append(f"{'開頭' if k.startswith('head') else '結尾'} {v}s")
            result = "設定 " + "、".join(parts)
        else:
            result = "頭尾無明顯靜音或已手動設好，未改動"
        print(f"  去頭尾：{result}")
        summary.append(("去頭去尾", result))

    print("\n══ 自動後製完成 ══")
    for step, res in summary:
        print(f"  • {step}：{res}")
    print("\n下一步：podcast edit 開 UI 人工確認 → podcast assemble 輸出。")
    return 0
