"""從分軌 speakers.json 自動建議時間版鏡頭切換點，寫成 cameras.json v2。

分軌集的 speakers.json 已知每張卡誰在講（speaker_key），且設計上 speaker_key == cam key，
所以 speaker→cam 幾近 1:1。把逐卡 speaker 用 carry-forward 收斂成「切換點」，省掉逐卡手標。
建議值非鎖定：使用者仍可在編輯器覆蓋少數例外（串音/同時講話判定不準時）。
"""
from __future__ import annotations

import sys

from podcast_toolkit import cameras_io, srt_io
from podcast_toolkit.episode import Episode


def run(ep: Episode, force: bool = False) -> int:
    speakers_json = ep.output_v2_speakers_json()
    cameras_json = ep.output_v2_cameras_json()
    v2_srt = ep.output_v2_srt()

    if not speakers_json.exists():
        print(
            f"✗ 找不到 {speakers_json.name}；分軌集才有 speakers，請先跑 podcast merge-per-mic",
            file=sys.stderr,
        )
        return 4
    if not v2_srt.exists():
        print(f"✗ 找不到 {v2_srt.name}；請先產生字幕", file=sys.stderr)
        return 3
    if cameras_json.exists() and not force:
        print(
            f"✗ 已存在 {cameras_json.name}；要覆蓋請加 --force（會先備份 .bak）",
            file=sys.stderr,
        )
        return 1

    speakers = cameras_io.load(speakers_json)
    cards = srt_io.parse(v2_srt.read_text(encoding="utf-8"))

    rule = ep.cfg.get("camera_rule") or {}
    home = str(rule.get("home", "a"))
    feature = {str(k): str(v) for k, v in (rule.get("feature") or {}).items()}
    min_sec = float(rule.get("min_sec", 15))
    transitions = cameras_io.suggest_camera_cuts(
        speakers, cards, home_cam=home, feature_cam=feature, min_sec=min_sec
    )

    # 覆蓋前先備份，誤建議也救得回來
    if cameras_json.exists():
        backup = cameras_json.with_suffix(cameras_json.suffix + ".bak")
        backup.write_text(cameras_json.read_text(encoding="utf-8"), encoding="utf-8")
    cameras_io.save_transitions(cameras_json, transitions)

    feat_desc = ", ".join(f"{sp}→cam {cam}" for sp, cam in sorted(feature.items())) or "(無)"
    print(
        f"✓ 規則 home=cam {home}、{feat_desc} 連講≥{min_sec:g}s 才切；"
        f"從 {len(speakers)} 張卡建議了 {len(transitions)} 個鏡頭切換點 "
        f"→ {cameras_json.name}（可在編輯器再覆蓋例外）"
    )
    return 0
