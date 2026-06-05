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
    """
    glossary = normalize_glossary(
        list(defaults.get("common_glossary") or []) + list(episode.get("glossary") or [])
    )
    user_fixes = list(defaults.get("common_fixes") or []) + list(episode.get("fixes") or [])
    auto_fixes = glossary_to_fixes(glossary)

    cfg = {
        "resegment": {**defaults["resegment"], **(episode.get("resegment") or {})},
        "subtitle_style": {**defaults["subtitle_style"], **(episode.get("subtitle_style") or {})},
        "gemini": {**(defaults.get("gemini") or {}), **(episode.get("gemini") or {})},
        "assets": dict(defaults["assets"]),
        "encode": dict(defaults["encode"]),
        # 使用者 fixes 在前（純錯字），glossary 展開的在後（保險絲）
        "fixes": user_fixes + auto_fixes,
        "glossary": glossary,
        "card_fixes": list(episode.get("card_fixes") or []),
        # episode 自身欄位
        "date": episode.get("date"),
        "name": episode.get("name"),
        "main_video": episode.get("main_video"),
        "main_srt": episode.get("main_srt"),
        "force_break": set(episode.get("force_break") or []),
        "force_join": set(episode.get("force_join") or []),
    }
    return cfg


def expand_placeholders(s: Optional[str], name: str) -> Optional[str]:
    """展開路徑欄位的 {name}；s 為 None 時透傳 None"""
    if s is None:
        return None
    return s.replace("{name}", name)
