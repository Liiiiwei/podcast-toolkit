"""分軌 SRT 合併：N 路 mic SRT → 統一 timeline + speakers sidecar。

上游：`gemini_subtitle.transcribe_per_mic` 已把每路 mic 過 VAD gate + Gemini → N 個
`04_工作檔/{name}_mic_{speaker}.srt`。
下游：UI 端讀 _final_v2.srt 顯示對白、讀 _final_v2.speakers.json 決定 speaker tag
（同時兩人講話時切上下兩行）。

合併策略：
  - 不合併 overlap cue：A 在講話、B 同時插話 → 兩個 cue 都保留，由 UI 端負責雙行渲染。
    為什麼不合併：合併就丟失誰先誰後 / 誰打斷誰的語意，UI 才有完整訊息決定怎麼排版。
  - 排序：start 升冪、同時間以 speaker key 字典序 tie-break（可重現、不靠 dict 順序）。
  - 重編 idx：合併後 1-based 連續，sidecar 用新 idx 當 key。
"""
from __future__ import annotations

import sys

from podcast_toolkit import cameras_io, srt_io
from podcast_toolkit.episode import Episode
from podcast_toolkit.fsutil import atomic_write_text


def merge_per_mic_srts(per_mic_srts: dict) -> tuple[str, dict]:
    """合併 N 路 mic SRT → (srt_text, speakers_mapping)。

    Args:
        per_mic_srts: {speaker_key: srt_path}，speaker key 對齊 cameras key
          （mic a ↔ cam a），方便下游切鏡與字幕標記用同一組 key。

    Returns:
        (srt_text, speakers_mapping)
          - srt_text: 重編 idx 後的 SRT 字串，依 start 升冪排序
          - speakers_mapping: {new_idx (int): speaker_key (str)} sidecar 內容

    Raises:
        ValueError: per_mic_srts 是空 dict
        FileNotFoundError: 某路 mic SRT 檔案不在
        RuntimeError: 某路 mic SRT 解析後 0 張卡（避免靜默產出空合併）
    """
    if not per_mic_srts:
        raise ValueError("merge 至少一路 mic SRT，傳了空 dict")

    all_cues: list[tuple[float, str, dict]] = []  # (start, speaker, card)
    for speaker, srt_path in per_mic_srts.items():
        if not srt_path.is_file():
            raise FileNotFoundError(f"找不到 mic_{speaker} SRT：{srt_path}")
        cards = srt_io.parse(srt_path.read_text(encoding="utf-8"))
        if not cards:
            raise RuntimeError(f"mic_{speaker} SRT 解析為空：{srt_path}")
        for c in cards:
            all_cues.append((c["start"], speaker, c))

    # start 升冪、同時間以 speaker key 字典序 tie-break
    all_cues.sort(key=lambda x: (x[0], x[1]))

    out_lines: list[str] = []
    speakers: dict = {}
    for new_idx, (_, speaker, card) in enumerate(all_cues, start=1):
        start_ts = srt_io.seconds_to_srt_ts(card["start"])
        end_ts = srt_io.seconds_to_srt_ts(card["end"])
        out_lines.append(f"{new_idx}\n{start_ts} --> {end_ts}\n{card['text']}\n")
        speakers[new_idx] = speaker
    return "\n".join(out_lines), speakers


def run(ep: Episode, force: bool = False) -> int:
    """讀所有 04_工作檔/{name}_mic_*.srt → 寫 03_成品/{name}_final_v2.srt + .speakers.json。

    用 `ep.mic_paths().keys()` 取 speaker 清單，不掃 glob 結果，避免拿到舊 speaker 殘檔。
    """
    mics = ep.mic_paths()
    if not mics:
        print("✗ episode.yaml 沒設 mics — srt_merge 是分軌專用，先設 mics 再跑", file=sys.stderr)
        return 4

    out_srt = ep.output_v2_srt()
    out_json = ep.output_v2_speakers_json()
    if out_srt.exists() and not force:
        print(f"✗ 已存在：{out_srt}", file=sys.stderr)
        print("  加 --force 覆寫（手動編輯過的版本會被蓋掉）", file=sys.stderr)
        return 1

    per_mic_srts = {sp: ep.per_mic_srt(sp) for sp in sorted(mics.keys())}
    try:
        srt_text, speakers = merge_per_mic_srts(per_mic_srts)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"✗ {e}", file=sys.stderr)
        print("  提示：先跑 `podcast subtitle --per-mic` 產生分軌 SRT", file=sys.stderr)
        return 4

    atomic_write_text(out_srt, srt_text)
    cameras_io.save(out_json, speakers)
    print(f"✓ 合併 {len(per_mic_srts)} 路 mic → {out_srt}")
    print(f"✓ speakers sidecar → {out_json}")
    return 0
