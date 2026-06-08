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


def _list_mother_videos(ep: Episode) -> list[str]:
    """掃 01_母帶/*.mp4 回相對路徑（含 cam A）；給 cam A select 用。"""
    mother_dir = ep.dir / "01_母帶"
    if not mother_dir.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(mother_dir.iterdir()):
        # DJI / iPhone 等相機常出大寫 .MP4，用 suffix.lower() 比對
        if not entry.is_file() or entry.suffix.lower() != ".mp4":
            continue
        out.append(str(entry.relative_to(ep.dir)))
    return out


def _list_cam_b_candidates(ep: Episode) -> list[str]:
    """掃 01_母帶/*.mp4 當 cam B 候選；排除 cam A 那一檔。"""
    cam_a_rel = (ep.cfg.get("cameras") or {}).get("a") or ep.cfg.get("main_video") or ""
    try:
        cam_a_resolved = ep.resolve_episode_path(cam_a_rel) if cam_a_rel else None
    except Exception:
        cam_a_resolved = None
    out: list[str] = []
    for rel in _list_mother_videos(ep):
        if cam_a_resolved and (ep.dir / rel) == cam_a_resolved:
            continue
        out.append(rel)
    return out


# 外接音檔常見副檔名（小寫比對，相機/錄音機常出大寫）
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".opus"}


def _list_audio_candidates(ep: Episode) -> list[str]:
    """找外接音檔候選；掃集根目錄 + 01_母帶/ + 02_素材/，回相對路徑。

    集根目錄：DAW / Audacity 直接輸出常落在這（如 Track1-Mic 1.wav）。
    04_工作檔/ 是 STT 暫存，03_成品/ 是輸出，都不列。
    """
    out: list[str] = []
    # 集根目錄頂層（不遞迴，避免抓到 04_工作檔/_grok_stt_*.mp3 暫存）
    for entry in sorted(ep.dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in _AUDIO_EXTS:
            continue
        out.append(entry.name)
    # 慣例子資料夾
    for sub in ("01_母帶", "02_素材"):
        d = ep.dir / sub
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in _AUDIO_EXTS:
                continue
            out.append(str(entry.relative_to(ep.dir)))
    return out


def _list_srt_candidates(ep: Episode) -> list[str]:
    """找字幕檔候選；掃集根目錄 + 03_成品/ + 04_工作檔/，回相對路徑。

    04_工作檔/<name>_v2.srt 是編輯器當前在編輯的；03_成品/<name>_final.srt
    是原始轉錄。讓使用者在 cam-modal 手動挑要拿哪份做最終合成。
    """
    out: list[str] = []
    for entry in sorted(ep.dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() != ".srt":
            continue
        out.append(entry.name)
    for sub in ("03_成品", "04_工作檔"):
        d = ep.dir / sub
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix.lower() != ".srt":
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
    # 字幕檔路徑（顯示用）：尊重 yaml srt_path override，否則 fallback _v2.srt；
    # cards 永遠來自 _v2.srt（編輯器只編這一份），override 只影響「最終合成讀哪份」
    active = ep.active_srt()
    try:
        srt_rel = str(active.relative_to(ep.dir))
    except ValueError:
        srt_rel = str(active)
    # cam A：展開 {name} placeholder 再回傳；若 candidates 裡找得到完全相同的就直接用，
    # 否則 fallback 到展開後的真實檔名，避免前端 select 出現帶 placeholder 的孤兒選項
    cam_a_raw = (ep.cfg.get("cameras") or {}).get("a") or ep.cfg.get("main_video") or ""
    if cam_a_raw:
        try:
            cam_a_abs = ep.resolve_episode_path(cam_a_raw)
            cam_a_rel = str(cam_a_abs.relative_to(ep.dir))
        except Exception:
            cam_a_rel = cam_a_raw
    else:
        cam_a_rel = ""
    if cards:
        _flag_suspicious_pause(
            cards,
            ep.cfg.get("suspicious_pause") or {},
            ep.cfg.get("resegment", {}).get("reaction_words") or [],
        )
    # 空集（init 完但沒放母帶）main_video 解析後可能不存在；前端拿這個旗標決定要不要
    # 顯示「請放檔案到 01_母帶/」的 empty banner，並跳過 <video> 自動 fetch。
    try:
        has_main_video = ep.main_video().is_file()
    except Exception:
        has_main_video = False
    return {
        "name": ep.name,
        "crop_yt": ep.cfg.get("crop_yt"),
        "crop_reels": ep.cfg.get("crop_reels"),
        "deletions": list(ep.cfg.get("deletions") or []),
        "head_trim_sec": float(ep.cfg.get("head_trim_sec") or 0),
        "tail_trim_sec": float(ep.cfg.get("tail_trim_sec") or 0),
        "reels_clips": list(ep.cfg.get("reels_clips") or []),
        "cards": cards,
        "needs_transcribe": needs_transcribe,
        "has_main_video": has_main_video,
        # T23a：雙鏡頭資訊（單機集 cameras 只有 a；前端要知道 b 在不在）
        "cameras": dict(ep.cfg.get("cameras") or {}),
        "camera_sync_offset": dict(ep.cfg.get("camera_sync_offset") or {}),
        "audio": ep.cfg.get("audio"),
        # 字幕卡 → 鏡頭對應表（只含 explicit 標過的；前端用 carry-forward 補其他卡）
        "cameras_mapping": cameras_io.load(ep.output_v2_cameras_json()),
        # T23a-followup：cam B 候選清單（前端下拉用，避免使用者手改 yaml）
        "cam_b_candidates": _list_cam_b_candidates(ep),
        # 外接音檔候選（.wav/.mp3/.m4a/...，掃 01_母帶 + 02_素材）
        "audio_candidates": _list_audio_candidates(ep),
        # 「最終合成檔案總覽」用：cam A 候選 + 目前 cam A + 字幕檔（read-only 顯示）
        "cam_a_candidates": _list_mother_videos(ep),
        "cam_a_path": cam_a_rel,
        "srt_path": srt_rel,
        # 字幕候選（_v2.srt + 原始轉錄 + 集根目錄 .srt）；前端 cam-modal 下拉用
        "srt_candidates": _list_srt_candidates(ep),
        # 前端 caption preview 用：對齊 ffmpeg ASS 實際輸出字幕大小（font_size / output_height）
        "subtitle_style": dict(ep.cfg.get("subtitle_style") or {}),
        "subtitle_style_reels": dict(ep.cfg.get("subtitle_style_reels") or {}),
        "output_resolution_yt": (ep.cfg.get("encode") or {}).get("resolution") or "1920x1080",
        "output_resolution_reels": "1080x1920",
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
            entry: dict[str, Any] = {
                "x": float(crop["x"]),
                "y": float(crop["y"]),
                "width": float(crop["width"]),
                "height": float(crop["height"]),
            }
            # cam B 獨立 crop（optional override；沒設就 fallback 用 base）
            crop_b = crop.get("b")
            if crop_b:
                entry["b"] = {
                    "x": float(crop_b["x"]),
                    "y": float(crop_b["y"]),
                    "width": float(crop_b["width"]),
                    "height": float(crop_b["height"]),
                }
            data[key] = entry
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

    # Reels 片段：list of {name, start_card, end_card}；空 list 清掉 key
    if "reels_clips" in payload:
        clips_raw = payload.get("reels_clips") or []
        clips_out: list[dict[str, Any]] = []
        for c in clips_raw:
            if not isinstance(c, dict):
                continue
            name = (c.get("name") or "").strip()
            if not name:
                continue
            try:
                start_card = int(c.get("start_card"))
                end_card = int(c.get("end_card"))
            except (TypeError, ValueError):
                continue
            clips_out.append({
                "name": name,
                "start_card": start_card,
                "end_card": end_card,
            })
        if clips_out:
            data["reels_clips"] = clips_out
        else:
            data.pop("reels_clips", None)

    # cam A 路徑：前端「最終合成總覽」可換 cam A。同步寫 cameras.a + main_video（保留舊欄位讓 fallback 路徑也對）。
    if "cam_a_path" in payload:
        cam_a_path = (payload.get("cam_a_path") or "").strip()
        if cam_a_path:
            cameras = dict(data.get("cameras") or {})
            cameras["a"] = cam_a_path
            data["cameras"] = cameras
            data["main_video"] = cam_a_path

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

    # 字幕檔路徑：cam-modal 手動選哪份 .srt 進最終合成。空字串 → 移除 key，回退預設 _v2.srt。
    if "srt_path" in payload:
        srt_path = (payload.get("srt_path") or "").strip()
        if srt_path:
            data["srt_path"] = srt_path
        else:
            data.pop("srt_path", None)

    # 外接音檔 + 同步偏移；用 key-presence 區分「沒動 UI」vs「明確清空」
    if "audio" in payload:
        audio_payload = payload.get("audio") or {}
        audio_path = (audio_payload.get("path") or "").strip()
        if audio_path:
            audio_entry: dict[str, Any] = {"path": audio_path}
            sync_audio = float(audio_payload.get("sync_offset") or 0)
            if sync_audio:
                audio_entry["sync_offset"] = sync_audio
            data["audio"] = audio_entry
        else:
            data.pop("audio", None)

    yaml_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    # _v2.srt 覆寫前先留一份滾動備份，避免誤存後找不回原稿
    # 還沒跑過 STT 的集會沒有 _v2.srt — 若同時也沒文字 override 就 no-op，
    # 讓使用者可以只先存「鏡頭/音檔/裁切」這類非字幕設定。
    v2 = ep.output_v2_srt()
    overrides = {
        int(c["idx"]): c["text"]
        for c in (payload.get("cards") or [])
        if c.get("text")
    }
    if v2.exists():
        original = v2.read_text(encoding="utf-8")
        backup = v2.with_suffix(v2.suffix + ".bak")
        backup.write_text(original, encoding="utf-8")
        cards = srt_io.parse(original)
        v2.write_text(srt_io.serialize(cards, overrides=overrides), encoding="utf-8")
    elif overrides:
        raise FileNotFoundError(
            f"找不到 {v2.name}，無法套用字幕文字修改；請先跑 podcast subtitle 產生字幕"
        )

    # T23a：字幕卡 → 鏡頭對應表 sidecar；前端傳回只含 explicit 標記的 mapping
    cameras_mapping = {
        int(k): str(v)
        for k, v in (payload.get("cameras_mapping") or {}).items()
        if v in ("a", "b")
    }
    cameras_io.save(ep.output_v2_cameras_json(), cameras_mapping)
