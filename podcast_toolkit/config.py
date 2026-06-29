"""設定載入與合併。

defaults.yaml 路徑相對於 toolkit_root；
episode.yaml 內路徑欄位相對於 episode 資料夾。
"""
import json
import os
import sys
from pathlib import Path
from typing import Optional
import yaml

from podcast_toolkit.constants import EPISODE_GLOSSARY_FILENAME


def toolkit_root() -> Path:
    """toolkit 安裝根目錄（defaults.yaml / assets 所在）。
    開發/editable：config.py 在 <root>/podcast_toolkit/config.py，parent.parent 是 root。
    py2app 打包：資料檔放在 .app/Contents/Resources（py2app 會設 sys.frozen + 環境變數 RESOURCEPATH）。
    """
    if getattr(sys, "frozen", False):
        rp = os.environ.get("RESOURCEPATH")
        if rp:
            return Path(rp)
        # 後備：從執行檔推 Contents/Resources（Contents/MacOS/python → ../Resources）
        return Path(sys.executable).resolve().parent.parent / "Resources"
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


def load_episode_glossary_sidecar(episode_dir: Path) -> list:
    """讀 <episode_dir>/.glossary.json（web UI 寫的集詞庫）。檔不存在/壞掉 → []。
    回傳 raw list，交給 normalize_glossary 統一格式。"""
    path = Path(episode_dir) / EPISODE_GLOSSARY_FILENAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def dedup_glossary(items: list) -> list:
    """以 canonical 為主鍵去重：sounds_like 取聯集、note 保留最後一個非空。
    讓 common + episode.yaml + .glossary.json 三來源合併後不重複、不漏 sounds_like。"""
    order = []
    by_key = {}
    for it in items:
        key = it["canonical"]
        if key not in by_key:
            by_key[key] = {"canonical": key, "sounds_like": list(it["sounds_like"]), "note": it["note"]}
            order.append(key)
        else:
            cur = by_key[key]
            for s in it["sounds_like"]:
                if s not in cur["sounds_like"]:
                    cur["sounds_like"].append(s)
            if it["note"]:
                cur["note"] = it["note"]
    return [by_key[k] for k in order]


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


def merge(defaults: dict, episode: dict, episode_glossary_sidecar: list = None) -> dict:
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

    # 三來源合併：defaults common_glossary + episode.yaml glossary + .glossary.json(web sidecar)。
    # 以 canonical 去重(sounds_like 取聯集)，讓 web 加的詞也進 cfg['glossary'] → proofread/resegment 都讀得到。
    glossary = dedup_glossary(normalize_glossary(
        list(defaults.get("common_glossary") or [])
        + list(episode.get("glossary") or [])
        + list(episode_glossary_sidecar or [])
    ))
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
        "proofread": {**(defaults.get("proofread") or {}), **(episode.get("proofread") or {})},
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
        # 字幕來源 override：cam-modal 手選哪份 .srt 進最終合成 / 編輯器下拉回顯；
        # 不設 → active_srt() fallback _v2.srt。先前漏列此 key → 寫進 yaml 也讀不回 cfg，
        # 導致 cam-modal 切字幕檔「存了卻跳回舊值」、最終合成也永遠讀 _v2.srt。
        "srt_path": episode.get("srt_path"),
        "cameras": dict(cameras),
        "camera_sync_offset": dict(episode.get("camera_sync_offset") or {}),
        "audio": episode.get("audio"),
        "mics": dict(episode.get("mics") or {}),
        # Breeze 分軌集標記：有逐卡講者標 → 前端渲染 speaker tag / 兩行。
        # 與「有無 mic 音檔路徑(mics)」正交：Breeze 集有講者標但沒 mic 路徑。
        "has_speaker_tags": bool(episode.get("has_speaker_tags") or False),
        "per_mic": {**(defaults.get("per_mic") or {}), **(episode.get("per_mic") or {})},
        "force_break": set(episode.get("force_break") or []),
        "force_join": set(episode.get("force_join") or []),
        "crop_yt": episode.get("crop_yt"),
        "crop_reels": episode.get("crop_reels"),
        # 節目封面 overlay 開關（沿用 watermark 機制：只疊正片、右上角）；defaults + episode 逐 key 合併
        "watermark": {**(defaults.get("watermark") or {}), **(episode.get("watermark") or {})},
        # 正片倍速（只加速正片，片頭尾不動）：{enabled, factor}
        "speed": {**(defaults.get("speed") or {}), **(episode.get("speed") or {})},
        # 全片去空拍（偵測中段靜音→跳剪）：{enabled, min_silence, pad, noise_db}
        "silence_trim": {
            **(defaults.get("silence_trim") or {}),
            **(episode.get("silence_trim") or {}),
        },
        # 畫面拉正旋轉（per cam，度數）：{a, b}
        "rotate": {**(defaults.get("rotate") or {}), **(episode.get("rotate") or {})},
        # 鏡頭自動建議規則：{home, feature:{speaker:cam}, min_sec}；episode 覆寫整段
        "camera_rule": {
            **(defaults.get("camera_rule") or {}),
            **(episode.get("camera_rule") or {}),
        },
        "deletions": list(episode.get("deletions") or []),
        # 時間版刪段（與字幕脫鉤）：[[start, end], ...] 秒；assemble.cut_intervals_from_cfg 優先吃它
        "cuts": list(episode.get("cuts") or []),
        # 刪段往前後延伸吃掉間隙雜音的秒數（每邊上限，夾在鄰卡邊界內）。episode 覆寫；0=關
        "cut_pad": float(episode.get("cut_pad", defaults.get("cut_pad", 0)) or 0),
        "head_trim_sec": float(episode.get("head_trim_sec") or 0),
        "tail_trim_sec": float(episode.get("tail_trim_sec") or 0),
        # 非破壞性字幕偏移（秒）：預覽 + 合成都套，原 _v2.srt 不動。漏列此 key 會像 srt_path
        # 一樣「寫進 yaml 卻讀不回 cfg」，導致存了沒生效。
        "subtitle_offset_sec": float(episode.get("subtitle_offset_sec") or 0),
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
