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


def merge(defaults: dict, episode: dict) -> dict:
    """合併 defaults 與 episode：
    - 純量：episode 覆寫 defaults
    - dict：逐 key 合併
    - list-of-pairs（fixes / card_fixes）：common + episode 串接，依序套用
    - 其他 list：episode 覆寫 defaults
    """
    cfg = {
        "resegment": {**defaults["resegment"], **(episode.get("resegment") or {})},
        "subtitle_style": {**defaults["subtitle_style"], **(episode.get("subtitle_style") or {})},
        "assets": dict(defaults["assets"]),
        "encode": dict(defaults["encode"]),
        # fixes: common + episode 串接（common 先套）
        "fixes": list(defaults.get("common_fixes") or []) + list(episode.get("fixes") or []),
        "card_fixes": list(episode.get("card_fixes") or []),
        # episode 自身欄位
        "date": episode.get("date"),
        "name": episode.get("name"),
        "main_video": episode.get("main_video"),
        "main_srt": episode.get("main_srt"),
        "force_break": set(episode.get("force_break") or []),
        "force_join": set(episode.get("force_join") or []),
        "crop": episode.get("crop"),
        "deletions": list(episode.get("deletions") or []),
    }
    return cfg


def expand_placeholders(s: Optional[str], name: str) -> Optional[str]:
    """展開路徑欄位的 {name}；s 為 None 時透傳 None"""
    if s is None:
        return None
    return s.replace("{name}", name)
