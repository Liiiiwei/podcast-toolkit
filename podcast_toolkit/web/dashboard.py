"""Dashboard 純函式：episode stage / recent / list_episodes。

不依賴 FastAPI，方便單元測試。
目前包含 episode_stage 與 recent 讀寫（後續 task 擴充 list_episodes）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from podcast_toolkit.episode import Episode

RECENT_KEY = "recent_episodes"
RECENT_MAX = 20


def episode_stage(ep_dir: Path) -> str:
    """回傳集數階段：broken / empty / needs_transcribe / needs_assemble / done。

    整段包 try：不只 Episode() 建構會炸（壞 yaml），後面的 .exists() 與
    延遲讀 cfg（權限錯、缺鍵）也可能拋——任何例外都標 broken，不上拋，
    否則會逸出到 /api/episodes 變 500（2026-08-08 feedback-signals-ux 第 3 節）。
    """
    try:
        ep = Episode(ep_dir)
        if not ep.main_video().exists():
            return "empty"
        if not ep.output_v2_srt().exists():
            return "needs_transcribe"
        if not (ep.output_yt_video().exists() or ep.output_reels_video().exists()):
            return "needs_assemble"
        return "done"
    except Exception:
        return "broken"


def _load_config_dict(config_path: Path) -> dict:
    """讀 config.json 為 dict；不存在或壞掉時回傳 {}。"""
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _atomic_write_json(config_path: Path, data: dict) -> None:
    """走 .tmp + os.replace，避免中途寫壞 config.json。"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, config_path)


def load_recent(config_path: Path) -> list[str]:
    """讀取最近開過的 episode 路徑清單；壞掉或缺檔回 []。"""
    cfg = _load_config_dict(config_path)
    raw = cfg.get(RECENT_KEY) or []
    return [str(p) for p in raw if isinstance(p, str)]


def save_recent(config_path: Path, recent: list[str]) -> None:
    """覆寫 recent_episodes（最多 RECENT_MAX 筆），保留 config 內其他欄位。"""
    cfg = _load_config_dict(config_path)
    cfg[RECENT_KEY] = recent[:RECENT_MAX]
    _atomic_write_json(config_path, cfg)


def add_recent(config_path: Path, path: str) -> None:
    """把 path 移到 recent 最前面（已存在則去重），超過 RECENT_MAX 自動截掉。"""
    recent = load_recent(config_path)
    recent = [p for p in recent if p != path]
    recent.insert(0, path)
    save_recent(config_path, recent)


def _episode_meta(ep_dir: Path) -> dict | None:
    """從一個 episode 資料夾抽出 dashboard card 需要的 metadata。
    回 None 代表這資料夾連 episode.yaml 都沒有，list_episodes 上層已過濾，
    這裡保留 return 型別讓未來新增 fail-cases 可以擴充。"""
    stage = episode_stage(ep_dir)
    name = ep_dir.name
    date = ""
    if " " in name and name[:8].isdigit():
        date = name[:8]
        name = name[9:]
    try:
        mtime = ep_dir.stat().st_mtime
    except OSError:
        mtime = 0
    return {
        "path": str(ep_dir),
        "name": name,
        "date": date,
        "stage": stage,
        "mtime": mtime,
    }


def list_episodes(roots: list[str], recent: list[str]) -> dict:
    """掃 roots + recent，回 {episodes: [...], warnings: [...]}。
    episodes 依 mtime 倒序；同一 path 去重。"""
    warnings: list[str] = []
    seen: dict[str, dict] = {}

    def _warn(msg: str) -> None:
        # 同一個壞資料夾可能同時出現在 roots 掃描與 recent，避免重複警告
        if msg not in warnings:
            warnings.append(msg)

    for raw_root in roots:
        root = Path(raw_root).expanduser()
        if not root.is_dir():
            warnings.append(f"找不到資料夾：{raw_root}")
            continue
        try:
            children = list(root.iterdir())
        except PermissionError:
            warnings.append(f"沒有權限讀取：{raw_root}")
            continue
        for child in children:
            # 逐 child 容錯：單一壞資料夾（如 chmod 000 導致 is_file() 拋
            # PermissionError）只跳過該集，不能讓整份 /api/episodes 回 500。
            # 失敗不靜默：跳過的一律寫進 warnings 給前端顯示。
            try:
                if not child.is_dir():
                    continue
                if not (child / "episode.yaml").is_file():
                    continue
                meta = _episode_meta(child)
            except Exception as e:
                _warn(f"讀不到 {child.name}：{e}")
                continue
            if meta is not None:
                seen[meta["path"]] = meta

    for raw_path in recent:
        ep_dir = Path(raw_path).expanduser()
        # recent 同款容錯：路徑失效（單純不存在）沿用既有行為靜默跳過，
        # 但權限錯這類「資料夾在、讀不到」要進 warnings，不准無聲消失。
        try:
            if not ep_dir.is_dir():
                continue
            if not (ep_dir / "episode.yaml").is_file():
                continue
            meta = _episode_meta(ep_dir)
        except Exception as e:
            _warn(f"讀不到 {ep_dir.name}：{e}")
            continue
        if meta is not None and meta["path"] not in seen:
            seen[meta["path"]] = meta

    episodes = sorted(seen.values(), key=lambda e: e["mtime"], reverse=True)
    return {"episodes": episodes, "warnings": warnings}
