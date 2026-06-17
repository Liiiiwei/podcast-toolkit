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


def _run_camera(ep: Episode) -> str:
    """鏡頭對應：需要 speakers.json。沒有就略過（單軌集的正常情況）。

    回一句人看的結果字串。AP2（speaker→camera 推導）落地後在這裡接上。
    """
    speakers = ep.output_v2_speakers_json()
    if not speakers.exists():
        return "略過（本集無分軌講者資料 speakers.json，鏡頭維持原設定）"
    try:
        from podcast_toolkit import autocamera  # AP2，尚未實作時優雅退場
    except ImportError:
        return "略過（偵測到 speakers.json，但自動鏡頭對應 AP2 尚未實作）"
    n = autocamera.run(ep)  # pragma: no cover - AP2 落地後才會走到
    return f"由 speakers.json 推出 {n} 段鏡頭對應"


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
        result = _run_camera(ep)
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
