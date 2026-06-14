"""設定載入與合併。

defaults.yaml 路徑相對於 toolkit_root；
episode.yaml 內路徑欄位相對於 episode 資料夾。
"""
from pathlib import Path
from typing import Optional
import yaml


def toolkit_root() -> Path:
    """toolkit 安裝根目錄。
    config.py 位在 <root>/podcast_toolkit/config.py，所以 parent.parent 是 root。
    """
    return Path(__file__).resolve().parent.parent


def load_defaults() -> dict:
    """讀 <toolkit_root>/defaults.yaml"""
    return yaml.safe_load((toolkit_root() / "defaults.yaml").read_text(encoding="utf-8"))


def load_episode(episode_dir: Path) -> dict:
    """讀 <episode_dir>/episode.yaml"""
    yaml_path = episode_dir / "episode.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(
            f"找不到 {yaml_path}。請先在這個資料夾跑 podcast init。"
        )
    return yaml.safe_load(yaml_path.read_text(encoding="utf-8"))


def normalize_glossary(items: list) -> list:
    """把 glossary 統一成 [{canonical, sounds_like, note}, ...]。
    支援純字串簡寫：'Liwei Sia' → {canonical: 'Liwei Sia', sounds_like: [], note: ''}
    """
    out = []
    for it in items or []:
        if isinstance(it, str):
            out.append({"canonical": it, "sounds_like": [], "note": ""})
        elif isinstance(it, dict) and it.get("canonical"):
            out.append({
                "canonical": it["canonical"],
                "sounds_like": list(it.get("sounds_like") or []),
                "note": it.get("note") or "",
            })
        # 格式不對的條目靜默略過（在 gemini_subtitle 載入時會印 warning）
    return out


def glossary_to_fixes(glossary: list) -> list:
    """把 normalized glossary 的 sounds_like → canonical 展開成 fix pairs。
    給 resegment 當保險絲：就算 Gemini 沒照 prompt 寫對，事後也會強制替換。
    """
    pairs = []
    for it in glossary:
        canonical = it.get("canonical")
        for sound in it.get("sounds_like") or []:
            if sound and sound != canonical:
                pairs.append([sound, canonical])
    return pairs


def merge(defaults: dict, episode: dict) -> dict:
    """合併 defaults 與 episode：
    - 純量：episode 覆寫 defaults
    - dict：逐 key 合併
    - list-of-pairs（fixes / card_fixes）：common + episode 串接，依序套用
    - glossary：通用+專屬串接，normalize 後同時：
        (1) 留在 cfg["glossary"] 給 Gemini prompt 用
        (2) 展開 sounds_like → canonical 疊到 cfg["fixes"] 尾端，給 resegment 保險用
    - 其他 list：episode 覆寫 defaults
    - crop_yt / crop_reels：YT 16:9 與 Reels 9:16 兩種裁切設定；
      舊 episode.yaml 只有 crop 時自動視為 crop_yt（一次性遷移）。
    - cameras：雙機資料 {a, b}；舊 episode.yaml 只有 main_video 時
      自動視為 cameras.a（單機模式）。
    - audio：獨立 stereo-mix 音檔 + 對齊參考（不設則沿用鏡頭原音）。
    - mics：分軌轉錄用的單人 mic 檔 {a, b, c, ...}；key 對齊 cameras key，
      不設 → 空 dict → 走原本的混音軌 Gemini 轉錄路線（向後相容）。
    - per_mic：分軌轉錄參數（VAD 閘門等），defaults + episode 逐 key 合併。
    """
    # cameras：episode["cameras"] 優先；否則 fallback main_video → cameras.a
    cameras = episode.get("cameras")
    if cameras is None:
        main_video = episode.get("main_video")
        cameras = {"a": main_video} if main_video else {}

    glossary = normalize_glossary(
        list(defaults.get("common_glossary") or []) + list(episode.get("glossary") or [])
    )
    user_fixes = list(defaults.get("common_fixes") or []) + list(episode.get("fixes") or [])
    auto_fixes = glossary_to_fixes(glossary)

    cfg = {
        "resegment": {**defaults["resegment"], **(episode.get("resegment") or {})},
        "suspicious_pause": {
            **defaults.get("suspicious_pause", {}),
            **(episode.get("suspicious_pause") or {}),
        },
        "subtitle_style": {**defaults["subtitle_style"], **(episode.get("subtitle_style") or {})},
        # Reels 專用字幕風格：defaults > subtitle_style_reels > subtitle_style（base） > episode override
        # 缺欄位時自動回退到 subtitle_style，讓只想微調幾欄的 episode 不用整段重抄
        "subtitle_style_reels": {
            **defaults["subtitle_style"],
            **(defaults.get("subtitle_style_reels") or {}),
            **(episode.get("subtitle_style") or {}),
            **(episode.get("subtitle_style_reels") or {}),
        },
        "gemini": {**(defaults.get("gemini") or {}), **(episode.get("gemini") or {})},
        "assets": dict(defaults["assets"]),
        # encode 可被 episode.yaml 局部覆寫（例：趕時間時 preset: medium 加速）
        "encode": {**defaults["encode"], **(episode.get("encode") or {})},
        # 使用者 fixes 在前（純錯字），glossary 展開的在後（保險絲）
        "fixes": user_fixes + auto_fixes,
        "glossary": glossary,
        "card_fixes": list(episode.get("card_fixes") or []),
        # episode 自身欄位
        "date": episode.get("date"),
        "name": episode.get("name"),
        "main_video": episode.get("main_video"),
        "main_srt": episode.get("main_srt"),
        "cameras": dict(cameras),
        "camera_sync_offset": dict(episode.get("camera_sync_offset") or {}),
        "audio": episode.get("audio"),
        "mics": dict(episode.get("mics") or {}),
        "per_mic": {**(defaults.get("per_mic") or {}), **(episode.get("per_mic") or {})},
        "force_break": set(episode.get("force_break") or []),
        "force_join": set(episode.get("force_join") or []),
        "crop_yt": episode.get("crop_yt"),
        "crop_reels": episode.get("crop_reels"),
        # 節目封面 overlay 開關（沿用 watermark 機制：只疊正片、右上角）；defaults + episode 逐 key 合併
        "watermark": {**(defaults.get("watermark") or {}), **(episode.get("watermark") or {})},
        # 正片倍速（只加速正片，片頭尾不動）：{enabled, factor}
        "speed": {**(defaults.get("speed") or {}), **(episode.get("speed") or {})},
        # 畫面拉正旋轉（per cam，度數）：{a, b}
        "rotate": {**(defaults.get("rotate") or {}), **(episode.get("rotate") or {})},
        "deletions": list(episode.get("deletions") or []),
        "head_trim_sec": float(episode.get("head_trim_sec") or 0),
        "tail_trim_sec": float(episode.get("tail_trim_sec") or 0),
        # Reels 片段截取：list of {name, start_card, end_card}
        # start_card / end_card 是 1-indexed 字幕卡編號（含頭含尾）
        "reels_clips": list(episode.get("reels_clips") or []),
    }
    # legacy 遷移：episode.yaml 還在用 crop → 視為 crop_yt
    if cfg["crop_yt"] is None and episode.get("crop") is not None:
        cfg["crop_yt"] = episode["crop"]
    return cfg


def expand_placeholders(s: Optional[str], name: str) -> Optional[str]:
    """展開路徑欄位的 {name}；s 為 None 時透傳 None"""
    if s is None:
        return None
    return s.replace("{name}", name)
