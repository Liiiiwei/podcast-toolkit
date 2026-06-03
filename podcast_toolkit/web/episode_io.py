"""把 Episode 物件 + _v2.srt 組成前端要的 JSON state，並負責寫回。"""
from __future__ import annotations
from pathlib import Path
from typing import Any

import yaml

from podcast_toolkit import cameras_io, srt_io
from podcast_toolkit.episode import Episode


def _flag_suspicious_pause(
    cards: list[dict],
    sus_cfg: dict,
    reaction_words: list[str],
) -> list[dict]:
    """對每張卡判 reaction_only / short_long / big_gap_before 三條規則，
    命中任一條就標 suspicious_pause=True，並把命中的原因塞進 suspicious_reasons。
    回傳同一份 cards（in-place 加欄位，再回傳，方便鏈式呼叫）。
    """
    max_chars = int(sus_cfg.get("short_long_max_chars", 3))
    min_dur = float(sus_cfg.get("short_long_min_dur_sec", 2.0))
    big_gap = float(sus_cfg.get("big_gap_min_sec", 1.5))
    reactions = {w.strip() for w in (reaction_words or [])}

    for i, c in enumerate(cards):
        text = (c.get("text") or "").replace("\n", "").strip()
        dur = float(c.get("end", 0)) - float(c.get("start", 0))
        gap_before = (
            float(c["start"]) - float(cards[i - 1]["end"]) if i > 0 else 0.0
        )

        reasons: list[str] = []
        if text and text in reactions:
            reasons.append("reaction_only")
        if len(text) < max_chars and dur > min_dur:
            reasons.append("short_long")
        if gap_before > big_gap:
            reasons.append("big_gap_before")

        c["suspicious_pause"] = bool(reasons)
        c["suspicious_reasons"] = reasons
    return cards


def _list_cam_b_candidates(ep: Episode) -> list[str]:
    """掃 01_母帶/*.mp4 當 cam B 候選；排除 cam A 那一檔。"""
    cam_a_rel = (ep.cfg.get("cameras") or {}).get("a") or ep.cfg.get("main_video") or ""
    try:
        cam_a_resolved = ep.resolve_episode_path(cam_a_rel) if cam_a_rel else None
    except Exception:
        cam_a_resolved = None
    mother_dir = ep.dir / "01_母帶"
    if not mother_dir.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(mother_dir.iterdir()):
        # DJI / iPhone 等相機常出大寫 .MP4，用 suffix.lower() 比對
        if not entry.is_file() or entry.suffix.lower() != ".mp4":
            continue
        if cam_a_resolved and entry == cam_a_resolved:
            continue
        out.append(str(entry.relative_to(ep.dir)))
    return out


def load_state(ep: Episode) -> dict[str, Any]:
    """讀 episode.yaml + _v2.srt → 給前端的初始狀態。

    新集還沒跑過 transcribe/resegment 時，回 needs_transcribe=True + cards=[]，
    讓前端引導使用者去轉字幕，而不是 500。
    """
    v2 = ep.output_v2_srt()
    needs_transcribe = not v2.exists()
    cards = [] if needs_transcribe else srt_io.parse(v2.read_text(encoding="utf-8"))
    if cards:
        _flag_suspicious_pause(
            cards,
            ep.cfg.get("suspicious_pause") or {},
            ep.cfg.get("resegment", {}).get("reaction_words") or [],
        )
    return {
        "name": ep.name,
        "crop_yt": ep.cfg.get("crop_yt"),
        "crop_reels": ep.cfg.get("crop_reels"),
        "deletions": list(ep.cfg.get("deletions") or []),
        "head_trim_sec": float(ep.cfg.get("head_trim_sec") or 0),
        "tail_trim_sec": float(ep.cfg.get("tail_trim_sec") or 0),
        "cards": cards,
        "needs_transcribe": needs_transcribe,
        # T23a：雙鏡頭資訊（單機集 cameras 只有 a；前端要知道 b 在不在）
        "cameras": dict(ep.cfg.get("cameras") or {}),
        "camera_sync_offset": dict(ep.cfg.get("camera_sync_offset") or {}),
        "audio": ep.cfg.get("audio"),
        # 字幕卡 → 鏡頭對應表（只含 explicit 標過的；前端用 carry-forward 補其他卡）
        "cameras_mapping": cameras_io.load(ep.output_v2_cameras_json()),
        # T23a-followup：cam B 候選清單（前端下拉用，避免使用者手改 yaml）
        "cam_b_candidates": _list_cam_b_candidates(ep),
    }


def save_state(ep: Episode, payload: dict[str, Any]) -> None:
    """把前端 payload 寫回：episode.yaml 的 crop_yt / crop_reels / deletions、覆寫 _v2.srt。"""
    yaml_path = ep.dir / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    # 清掉舊欄位（一次性遷移）
    data.pop("crop", None)

    for key in ("crop_yt", "crop_reels"):
        crop = payload.get(key)
        if crop:
            data[key] = {
                "x": float(crop["x"]),
                "y": float(crop["y"]),
                "width": float(crop["width"]),
                "height": float(crop["height"]),
            }
        else:
            data.pop(key, None)

    # deletions
    deletions = list(payload.get("deletions") or [])
    if deletions:
        data["deletions"] = [int(i) for i in deletions]
    else:
        data.pop("deletions", None)

    # head / tail trim：> 0 才寫，否則清掉避免噪音
    for key in ("head_trim_sec", "tail_trim_sec"):
        val = float(payload.get(key) or 0)
        if val > 0:
            data[key] = val
        else:
            data.pop(key, None)

    # T23a-followup：cam B 路徑（前端 UI 寫入；用 key-presence 區分「沒動 UI」vs「明確清空」）
    if "cam_b_path" in payload:
        cam_b_path = (payload.get("cam_b_path") or "").strip()
        cameras = dict(data.get("cameras") or {})
        if cam_b_path:
            cam_a_path = cameras.get("a") or data.get("main_video") or ep.cfg.get("cameras", {}).get("a")
            cameras["a"] = cam_a_path
            cameras["b"] = cam_b_path
            data["cameras"] = cameras
        else:
            cameras.pop("b", None)
            if cameras:
                data["cameras"] = cameras
            else:
                data.pop("cameras", None)

    # T23a-followup：cam B sync offset；0 / 空值 → 整段移除
    if "camera_sync_offset_b" in payload:
        sync_b = float(payload.get("camera_sync_offset_b") or 0)
        if sync_b:
            data["camera_sync_offset"] = {"b": sync_b}
        else:
            data.pop("camera_sync_offset", None)

    yaml_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    # _v2.srt 覆寫前先留一份滾動備份，避免誤存後找不回原稿
    v2 = ep.output_v2_srt()
    original = v2.read_text(encoding="utf-8")
    backup = v2.with_suffix(v2.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")

    cards = srt_io.parse(original)
    overrides = {
        int(c["idx"]): c["text"]
        for c in (payload.get("cards") or [])
        if c.get("text")
    }
    v2.write_text(srt_io.serialize(cards, overrides=overrides), encoding="utf-8")

    # T23a：字幕卡 → 鏡頭對應表 sidecar；前端傳回只含 explicit 標記的 mapping
    cameras_mapping = {
        int(k): str(v)
        for k, v in (payload.get("cameras_mapping") or {}).items()
        if v in ("a", "b")
    }
    cameras_io.save(ep.output_v2_cameras_json(), cameras_mapping)
