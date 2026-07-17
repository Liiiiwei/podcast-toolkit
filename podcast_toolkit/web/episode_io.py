"""把 Episode 物件 + _v2.srt 組成前端要的 JSON state，並負責寫回。"""
from __future__ import annotations
from pathlib import Path
from typing import Any

import yaml

from podcast_toolkit import cameras_io, srt_io
from podcast_toolkit.fsutil import atomic_write_text
from podcast_toolkit.constants import AUDIO_EXTS
from podcast_toolkit.episode import Episode
from podcast_toolkit.resegment import _HALF_SENTENCE_TAIL
from podcast_toolkit.web.shared import PREVIEWABLE_EXTS, SKIP_DIRS, TRANSCRIBABLE_EXTS
from podcast_toolkit.whisper_guard import GuardConfig, WhisperGuard


def _flag_review(
    cards: list[dict],
    rcfg: dict,
    guard: WhisperGuard,
) -> list[dict]:
    """重算 resegment.py 寫進 _resegment_review.txt 的兩條「待複查」旗標，
    讓前端不用開文字檔就能在卡片上看到 ⚠：

    - half_sentence：句子斷在連接詞/介詞上（像沒講完），判斷對齊 resegment.py。
    - repetition：whisper 重複幻覺（guard.is_repetitive）。

    與 _flag_suspicious_pause 並列、用獨立欄位（needs_review / review_reasons），
    刻意不塞進 suspicious_pause，避免紅卡批次刪除 toolbar 誤收這些卡。
    in-place 加欄位後回傳同一份 cards。
    """
    dangle = tuple(rcfg.get("dangle_endings") or ())
    for c in cards:
        txt = (c.get("text") or "").replace("\n", "").strip()
        reasons: list[str] = []
        if len(txt) >= 4 and (txt[-1] in _HALF_SENTENCE_TAIL or (dangle and txt.endswith(dangle))):
            reasons.append("half_sentence")
        if guard.is_repetitive(txt):
            reasons.append("repetition")
        c["needs_review"] = bool(reasons)
        c["review_reasons"] = reasons
    return cards


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
        if entry.suffix.lower() not in AUDIO_EXTS:
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
            if entry.suffix.lower() not in AUDIO_EXTS:
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
        rcfg = ep.cfg.get("resegment") or {}
        _flag_suspicious_pause(
            cards,
            ep.cfg.get("suspicious_pause") or {},
            rcfg.get("reaction_words") or [],
        )
        # resegment 的「待複查」旗標（半句結尾 / 重複幻覺）→ 前端卡片顯示 ⚠
        guard = WhisperGuard(
            GuardConfig(
                char_loop_min_repeats=(rcfg.get("whisper_guard") or {}).get(
                    "char_loop_min_repeats", 3
                )
            )
        )
        _flag_review(cards, rcfg, guard)
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
        # 旋轉拉正（per cam 度數）/ 節目封面開關 / 正片倍速：前端編輯介面用
        "rotate": dict(ep.cfg.get("rotate") or {}),
        "cover_enabled": bool((ep.cfg.get("watermark") or {}).get("enabled")),
        "speed": dict(ep.cfg.get("speed") or {}),
        "silence_trim": dict(ep.cfg.get("silence_trim") or {}),
        "deletions": list(ep.cfg.get("deletions") or []),
        # 時間版刪段（B1）：save_state 寫得進、這裡也要讀得回，否則前端看不到也砍不掉
        # 已存的 cuts（它們在合成時仍會生效 → 看不到又移不掉）。
        "cuts": list(ep.cfg.get("cuts") or []),
        "head_trim_sec": float(ep.cfg.get("head_trim_sec") or 0),
        "tail_trim_sec": float(ep.cfg.get("tail_trim_sec") or 0),
        # 非破壞性字幕偏移（秒）：預覽 + 合成都套，原 _v2.srt 不動。正值=字幕往後延。
        "subtitle_offset_sec": float(ep.cfg.get("subtitle_offset_sec") or 0),
        "reels_clips": list(ep.cfg.get("reels_clips") or []),
        "cards": cards,
        "needs_transcribe": needs_transcribe,
        "has_main_video": has_main_video,
        # T23a：雙鏡頭資訊（單機集 cameras 只有 a；前端要知道 b 在不在）
        "cameras": dict(ep.cfg.get("cameras") or {}),
        "camera_sync_offset": dict(ep.cfg.get("camera_sync_offset") or {}),
        # 鏡頭規則（home/feature:{speaker:cam}/min_sec）：分軌設定 modal 回填角色用
        "camera_rule": dict(ep.cfg.get("camera_rule") or {}),
        "audio": ep.cfg.get("audio"),
        # 鏡頭已改時間版切換點（與字幕脫鉤）；載入時吸附到當下卡 → idx→cam 給前端顯示。
        # 換字幕後吸附到新斷句最近的卡，前端維持「卡 key」介面不變。
        "cameras_mapping": cameras_io.transitions_to_card_mapping(
            cameras_io.load_transitions(ep.output_v2_cameras_json(), cards), cards
        ),
        # 分軌 mic 設定（前端拿來決定要不要渲染 speaker tag / ruler；空 dict = 單軌集）
        "mics": dict(ep.cfg.get("mics") or {}),
        # Breeze 分軌集標記：cfg 明確設了 has_speaker_tags，或集已有 speakers.json
        # （ingest-breeze 寫的）→ 視為有講者標，前端渲染 speaker tag / 兩行。
        # 與 mics 正交：Breeze 集 mics 為空但有 speakers.json。
        "has_speaker_tags": bool(ep.cfg.get("has_speaker_tags"))
        or ep.output_v2_speakers_json().exists(),
        # 已存在的分軌 SRT（04_工作檔/{name}_mic_{speaker}.srt）— UI 勾選 modal 用來顯示「已轉/未轉」
        "mic_srt_existing": sorted(
            sp for sp in (ep.cfg.get("mics") or {})
            if ep.per_mic_srt(sp).exists()
        ),
        # 字幕卡 → speaker 對應表（sidecar 由 srt_merge 產出；shape 同 cameras_mapping）
        "speakers_mapping": cameras_io.load(ep.output_v2_speakers_json()),
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


def _parse_composite_id(key: Any) -> tuple[int, int]:
    """前端 cameras_mapping / deletions / splits 的 key 可能是純 int '3' 或 split 後子卡 '5:1'。
    一律解成 (old_srt_idx, part_idx)；未切的卡 part_idx 固定 0。
    """
    s = str(key)
    if ":" in s:
        oid_s, part_s = s.split(":", 1)
        return int(oid_s), int(part_s)
    return int(s), 0


def save_mics_config(
    ep: Episode,
    mics: dict[str, str],
    roles: dict[str, str] | None = None,
    min_sec: float | None = None,
) -> None:
    """把 mics 設定（speaker → path）寫進 episode.yaml 的 mics: 區塊。

    只動 mics 欄位，其他欄位保持原樣（safe_load → 改 → safe_dump）。
    不檢查路徑是否存在 — 那是呼叫端的責任（api 層先檢過了）。

    給 roles（{speaker: "host"|"guest"}）時，一併生成 camera_rule（簡化版）：
    cam A = home（全景，主持一律留 A）；標 guest 的軌 → cam B（來賓特寫）；
    來賓連續講滿 min_sec 秒才切到 B。來賓軌號每集不同 → 由 roles 動態決定，不寫死。
    """
    yaml_path = ep.dir / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    data["mics"] = {sp: mics[sp] for sp in sorted(mics)}
    if roles is not None:
        guests = sorted(
            sp for sp, r in roles.items() if r == "guest" and sp in mics
        )
        data["camera_rule"] = {
            "home": "a",
            "feature": {sp: "b" for sp in guests},
            "min_sec": float(min_sec) if min_sec else 15.0,
        }
    atomic_write_text(
        yaml_path,
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
    )


def save_state(ep: Episode, payload: dict[str, Any]) -> None:
    """把前端 payload 寫回：episode.yaml 的 crop_yt / crop_reels / deletions、覆寫 _v2.srt。

    splits payload：{"5": ["前半文字", "後半文字"]} → save 時把第 5 卡切成兩張、整份 SRT 重編號。
    cameras_mapping / deletions 的 key 可帶 ":part" 指向子卡（如 "5:1"），save 時一併翻譯成新編號。
    """
    yaml_path = ep.dir / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    # 清掉舊欄位（一次性遷移）
    data.pop("crop", None)

    for key in ("crop_yt", "crop_reels"):
        if key not in payload:
            continue  # 局部存檔沒帶這個 key → 不動既有值（明確送 null/空才清除，避免靜默抹掉裁切）
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

    # 旋轉拉正（per cam，度數）：a/b 任一非 0 才寫；全 0 → 移除整個 rotate key
    if "rotate" in payload:
        rot_payload = payload.get("rotate") or {}
        rot_out: dict[str, float] = {}
        for cam in ("a", "b"):
            try:
                v = float(rot_payload.get(cam) or 0)
            except (TypeError, ValueError):
                v = 0.0
            if abs(v) > 1e-6:
                rot_out[cam] = v
        if rot_out:
            data["rotate"] = rot_out
        else:
            data.pop("rotate", None)

    # 節目封面開關：明確寫 enabled bool。預設已開（defaults.yaml），所以「關」要寫
    # explicit false 才壓得過預設（pop 掉會回退成預設的 true）。
    if "cover_enabled" in payload:
        data["watermark"] = {"enabled": bool(payload.get("cover_enabled"))}

    # 正片倍速：enabled 時寫 {enabled, factor}（夾在 0.5–2.0）；關閉 → 移除整段（回退預設不加速）
    if "speed" in payload:
        sp = payload.get("speed") or {}
        if sp.get("enabled"):
            try:
                factor = float(sp.get("factor") or 1.25)
            except (TypeError, ValueError):
                factor = 1.25
            data["speed"] = {"enabled": True, "factor": min(2.0, max(0.5, factor))}
        else:
            data.pop("speed", None)

    # 全片去空拍：enabled 時寫 {enabled, min_silence}；關閉 → 移除整段（回退預設不去空拍）
    if "silence_trim" in payload:
        st = payload.get("silence_trim") or {}
        if st.get("enabled"):
            try:
                min_sil = float(st.get("min_silence") or 0.8)
            except (TypeError, ValueError):
                min_sil = 0.8
            data["silence_trim"] = {
                "enabled": True,
                "min_silence": min(5.0, max(0.3, min_sil)),
            }
        else:
            data.pop("silence_trim", None)

    # deletions：先收原始 composite IDs，等 SRT 重編號完再翻譯成新 int idx
    raw_deletions = list(payload.get("deletions") or [])

    # 時間版刪段（cuts）：[[start, end], ...] 秒（與字幕脫鉤、不需翻譯卡 idx）。有值寫、空清掉。
    # 前端改用時間版刪段後送 cuts；舊 per-card deletions 仍走下面那條，cut_intervals_from_cfg
    # 兩者都吃且 cuts 優先。容許 [s,e] 或 {start,end}，丟掉非法/零長度。
    if "cuts" in payload:
        norm = []
        for c in (payload.get("cuts") or []):
            try:
                if isinstance(c, dict):
                    s, e = float(c["start"]), float(c["end"])
                else:
                    s, e = float(c[0]), float(c[1])
            except (TypeError, ValueError, KeyError, IndexError):
                continue
            if e > s:
                norm.append([round(max(0.0, s), 3), round(e, 3)])
        if norm:
            data["cuts"] = sorted(norm)
        else:
            data.pop("cuts", None)

    # head / tail trim：> 0 才寫，否則清掉避免噪音
    for key in ("head_trim_sec", "tail_trim_sec"):
        if key not in payload:
            continue  # 局部存檔沒帶這個 key → 不動既有值（避免靜默抹掉片頭/片尾 trim）
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

    # 字幕字級：只調 font_size override（不整段覆寫 subtitle_style）。等於 defaults →
    # 移除 font_size，避免 yaml 殘留跟預設相同的冗餘值。YT 與 Reels 各自存。
    if "subtitle_style" in payload or "subtitle_style_reels" in payload:
        from podcast_toolkit import config as _config
        _defaults = _config.load_defaults()
        for _key in ("subtitle_style", "subtitle_style_reels"):
            if _key not in payload:
                continue
            _fs = (payload.get(_key) or {}).get("font_size")
            if _fs in (None, ""):
                continue
            _fs = int(round(float(_fs)))
            _default_fs = int(float((_defaults.get(_key) or {}).get("font_size") or 0))
            _block = dict(data.get(_key) or {})
            if _fs == _default_fs:
                _block.pop("font_size", None)
            else:
                _block["font_size"] = _fs
            if _block:
                data[_key] = _block
            else:
                data.pop(_key, None)

    # 非破壞性字幕偏移（秒）：存參數而非覆寫 srt；預覽 + 合成都套。0 → 移除 key。
    if "subtitle_offset_sec" in payload:
        sub_off = float(payload.get("subtitle_offset_sec") or 0)
        if abs(sub_off) >= 1e-6:
            data["subtitle_offset_sec"] = sub_off
        else:
            data.pop("subtitle_offset_sec", None)

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

    # _v2.srt 覆寫前先留一份滾動備份，避免誤存後找不回原稿
    # 還沒跑過 STT 的集會沒有 _v2.srt — 若同時也沒文字 override / splits 就 no-op，
    # 讓使用者可以只先存「鏡頭/音檔/裁切」這類非字幕設定。
    v2 = ep.output_v2_srt()
    overrides = {
        int(c["idx"]): c["text"]
        for c in (payload.get("cards") or [])
        if c.get("text")
    }
    # splits payload key 是 str(old_idx)，至少 2 段才算真的切；< 2 段忽略
    raw_splits = payload.get("splits") or {}
    splits: dict[int, list[str]] = {}
    for k, v in raw_splits.items():
        if not isinstance(v, list) or len(v) < 2:
            continue
        try:
            splits[int(k)] = [str(p) for p in v]
        except (TypeError, ValueError):
            continue

    # merges：要併進上一張卡的 old idx 清單（前端 Backspace 跨卡合併）。被併卡不單獨輸出，
    # 只把結束時間接到上一張；合併後文字由 overrides 落在上一張卡（見 srt_io.serialize_with_map）。
    merges: set[int] = set()
    for k in payload.get("merges") or []:
        try:
            merges.add(int(k))
        except (TypeError, ValueError):
            continue

    # 單卡時間微調：兩個來源都收進 composite-key（idx, part）dict：
    #  (a) time_overrides payload（int idx，舊版拖拉）— 非法值靜默跳過
    #  (b) card_timings payload（composite "5" / "5:1"）— 後端權威驗證，非法 raise（轉 400）
    time_overrides: dict[tuple[int, int], tuple[float, float]] = {}
    for k, v in (payload.get("time_overrides") or {}).items():
        if not isinstance(v, dict):
            continue
        try:
            s = float(v.get("start"))
            e = float(v.get("end"))
        except (TypeError, ValueError):
            continue
        if e <= s:  # 終點不得早於起點，丟掉非法值
            continue
        try:
            time_overrides[(int(k), 0)] = (max(0.0, s), e)
        except (TypeError, ValueError):
            continue
    for k, v in (payload.get("card_timings") or {}).items():
        if not isinstance(v, dict):
            continue
        try:
            st = float(v.get("start"))
            en = float(v.get("end"))
        except (TypeError, ValueError):
            raise ValueError(f"字幕時間不是數字：{k} → {v!r}")
        if not (st >= 0 and st < en):
            raise ValueError(
                f"字幕時間不合法（需 0 ≤ 開始 < 結束）：{k} → {st:.3f}–{en:.3f}"
            )
        time_overrides[_parse_composite_id(k)] = (st, en)

    # 新增字卡：[{start, end, text}]；append 進現有卡、依時間排序後一起重編號
    new_cards: list[dict] = []
    for nc in payload.get("new_cards") or []:
        if not isinstance(nc, dict):
            continue
        try:
            s = float(nc.get("start"))
            e = float(nc.get("end"))
        except (TypeError, ValueError):
            continue
        if e <= s:
            continue
        new_cards.append({"start": max(0.0, s), "end": e, "text": str(nc.get("text") or "")})

    # 預設 lookup：未切 + 未改的卡，old idx == new idx；遇到 v2.srt 不存在但又有狀態要存時也能 fallback
    idx_lookup: dict[tuple[int, int], int] = {}
    if v2.exists():
        original = v2.read_text(encoding="utf-8")
        backup = v2.with_suffix(v2.suffix + ".bak")
        atomic_write_text(backup, original)
        cards = srt_io.parse(original)
        # 套用時間微調（在 serialize 前改 card.start/end；切過的卡 override 會當 t0/t1 重算 sub-card）
        if time_overrides:
            for c in cards:
                ov = time_overrides.get((int(c["idx"]), 0))
                if ov:
                    c["start"], c["end"] = ov
        # 插入新字卡：給暫時 idx（接在最大 idx 後）；serialize_with_map 會重編號
        if new_cards:
            max_idx = max((int(c["idx"]) for c in cards), default=0)
            for i, nc in enumerate(new_cards):
                cards.append({"idx": max_idx + 1 + i, **nc})
        # 套完時間 override / 插入新卡後「一律」依 start 重排：SRT 必須時間單調，重新編號才會跟
        # 前端（也是依 start 排）對得上。拖拉換位置 / 微調把卡移過鄰居時，少了這步會寫出非單調
        # SRT、重載後整份錯位（症狀：「時間整個跑掉」）。輸入已排序時重排為 no-op，安全。
        cards.sort(key=lambda c: float(c["start"]))
        new_text, idx_map = srt_io.serialize_with_map(
            cards, overrides=overrides, splits=splits,
            time_overrides=time_overrides, merges=merges,
        )
        atomic_write_text(v2, new_text)
        for i, key in enumerate(idx_map):
            idx_lookup[key] = i + 1
    elif overrides or splits or time_overrides or new_cards or merges:
        raise FileNotFoundError(
            f"找不到 {v2.name}，無法套用字幕文字／時間修改；請先跑 podcast subtitle 產生字幕"
        )

    def _translate(key: Any) -> int | None:
        """composite id → 新 1-based idx；找不到（卡已不存在 / lookup 為空）回 None。
        v2.srt 不存在時 lookup 為空 → 直接用原 int（向後相容沒字幕也能存 deletions/cameras_mapping）。
        """
        oid, part = _parse_composite_id(key)
        if idx_lookup:
            return idx_lookup.get((oid, part))
        return oid if part == 0 else None

    # deletions 翻譯（過濾 None，避免被切掉的舊卡留殘影）
    new_deletions: list[int] = []
    for k in raw_deletions:
        try:
            nid = _translate(k)
        except (TypeError, ValueError):
            continue
        if nid is not None:
            new_deletions.append(nid)
    if new_deletions:
        data["deletions"] = new_deletions
    else:
        data.pop("deletions", None)

    # yaml 在 SRT 寫完 + deletions 翻完後才落地，避免中途崩潰留下不一致狀態
    atomic_write_text(
        yaml_path,
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
    )

    # 鏡頭：前端傳回 idx→cam（carry-forward 標記）。翻成新 idx 後，用剛寫好的 v2 卡把每個
    # idx 解析成「卡起始時間」，存成**時間版**切換點 [{t, cam}]（與字幕脫鉤，換字幕不錯位）。
    new_cameras_mapping: dict[int, str] = {}
    for k, v in (payload.get("cameras_mapping") or {}).items():
        if v not in ("a", "b"):
            continue
        try:
            nid = _translate(k)
        except (TypeError, ValueError):
            continue
        if nid is not None:
            new_cameras_mapping[nid] = str(v)
    v2_cards = srt_io.parse(v2.read_text(encoding="utf-8")) if v2.exists() else []
    cam_transitions = cameras_io.card_mapping_to_transitions(new_cameras_mapping, v2_cards)
    cameras_io.save_transitions(ep.output_v2_cameras_json(), cam_transitions)

    # 分軌 speaker sidecar：使用者在前端手動改 speaker tag 後一併存回。
    # valid speaker keys 由 episode.yaml.mics 決定（不是寫死 a/b），保留三人以上集的擴充空間。
    #
    # 只有「確實設了 mics」的分軌集才由編輯器管理 speakers.json。沒有 mics 的集（單軌 /
    # yaml 未設 mics 卻有孤兒 sidecar）一律不碰：前端會把 speakersMapping 過濾成空
    # （validSpeakers 為空），若照寫就會 cameras_io.save({}) → 把既有 speakers.json 誤刪。
    # 這正是「有 speakers.json 但 yaml 沒 mics 的集，一存檔講者資料就不見」的 bug。
    valid_speakers = set((ep.cfg.get("mics") or {}).keys())
    if valid_speakers:
        new_speakers_mapping: dict[int, str] = {}
        for k, v in (payload.get("speakers_mapping") or {}).items():
            if v not in valid_speakers:
                continue
            try:
                nid = _translate(k)
            except (TypeError, ValueError):
                continue
            if nid is not None:
                new_speakers_mapping[nid] = str(v)
        cameras_io.save(ep.output_v2_speakers_json(), new_speakers_mapping)


def _list_episode_files(root: Path) -> list[dict]:
    """遞迴列出集資料夾內所有檔案，標註 kind / 字幕角色。"""
    files: list[dict] = []
    try:
        ep = Episode(root)
        main_video_path = ep.main_video()
        main_srt_path = ep.main_srt()
        # active_srt 反映 cam-modal 手選；override 沒設時仍會等於 _v2.srt
        active_srt_path = ep.active_srt()
        yt_out = ep.output_yt_video()
        reels_out = ep.output_reels_video()
    except Exception:
        main_video_path = main_srt_path = active_srt_path = None
        yt_out = reels_out = None

    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS or part.startswith(".") for part in p.relative_to(root).parts):
            continue
        rel = str(p.relative_to(root))
        try:
            size = p.stat().st_size
        except OSError:
            size = 0

        first = p.relative_to(root).parts[0] if p.relative_to(root).parts else ""
        kind = "other"
        is_active_srt = False
        is_main_srt_backup = False

        if main_video_path and p == main_video_path:
            kind = "main_video"
        elif active_srt_path and p == active_srt_path:
            kind = "subtitle"
            is_active_srt = True
        elif main_srt_path and p == main_srt_path:
            kind = "subtitle"
            is_main_srt_backup = True
        elif (yt_out and p == yt_out) or (reels_out and p == reels_out):
            kind = "composite"
        elif p.suffix.lower() == ".srt":
            kind = "subtitle"
        elif first == "01_母帶":
            kind = "master"
        elif first == "04_工作檔":
            kind = "work"

        files.append({
            "path": rel,
            "size": size,
            "transcribable": p.suffix.lower() in TRANSCRIBABLE_EXTS,
            "previewable": p.suffix.lower() in PREVIEWABLE_EXTS,
            "kind": kind,
            "is_active_srt": is_active_srt,
            "is_main_srt_backup": is_main_srt_backup,
        })
    return files
