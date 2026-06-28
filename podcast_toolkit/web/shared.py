"""web 層共用：常數、glossary/資產 helper、路徑驗證、路由 context。

設定檔（config.json / typo-dict.json）的存取 helper 留在 api.py——
測試會 monkeypatch api 模組上的那些名稱，build_app 在呼叫當下把引用
打包進 RouteContext，路由模組一律透過 ctx 拿。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import HTTPException

from podcast_toolkit import config as pt_config
from podcast_toolkit.episode import Episode

STATIC_DIR = Path(__file__).resolve().parent / "static"
COMMON_GLOSSARY_PATH = Path.home() / ".podcast-toolkit" / "common-glossary.json"
EPISODE_GLOSSARY_FILENAME = ".glossary.json"

# 可轉字幕的副檔名（含音訊與含音訊軌的影片）
TRANSCRIBABLE_EXTS = {
    ".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus",
    ".mp4", ".mov", ".mkv", ".webm",
}
# 可在瀏覽器直接預覽的影片副檔名
PREVIEWABLE_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
# 可在瀏覽器直接預覽的音檔副檔名 + MIME 對照
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".opus"}
AUDIO_MIME = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
}
# 列檔時忽略的目錄/檔名片段
SKIP_DIRS = {".DS_Store", "__pycache__", ".git"}


def probe_static_access() -> str | None:
    """偵測 macOS TCC「能 stat、不能 open」：toolkit 裝在 ~/Desktop /Downloads
    /Documents 等受保護資料夾時，server 收到 GET 會先用 os.stat 取大小送出
    Content-Length（TCC 允許），再 open() 讀 body 卻被擋 → header 已送、body 永
    不送 → 瀏覽器 ERR_CONTENT_LENGTH_MISMATCH、整頁空白卻無明顯錯誤。

    這裡在 server 啟動時主動探一次 static/app.js：能 stat 但 open 噴
    PermissionError 即判定被 TCC 擋，回傳被擋的目錄字串給 UI 報錯；可正常
    讀取回 None。其他錯誤（檔案不存在等）不在本偵測範圍，一律回 None。
    """
    probe = STATIC_DIR / "app.js"
    try:
        st = probe.stat()
    except OSError:
        return None
    if not st.st_size:
        return None
    try:
        with open(probe, "rb") as fh:
            fh.read(1)
    except PermissionError:
        return str(STATIC_DIR)
    except OSError:
        return None
    return None


@dataclass
class RouteContext:
    """build_app 打包給各路由模組的共用依賴。

    holder 用 dict 包住 Episode，讓 /api/episode/switch 等能 hot-swap。
    load_config 等 callable 是 lambda：呼叫當下才查 api.py 的模組 global，
    所以測試在 build_app 前後 monkeypatch api 模組都會生效。
    """
    holder: dict
    shutdown: Callable[[], None]
    load_config: Callable[[], dict]
    save_config: Callable[[dict], None]
    load_typo_dict: Callable[[], list]
    save_typo_dict: Callable[[list], None]
    get_config_path: Callable[[], Path]

    def require_ep(self) -> Episode:
        ep = self.holder["ep"]
        if ep is None:
            raise HTTPException(status_code=409, detail="尚未選集，請先在 Dashboard 選一集")
        return ep


def validate_episode_path(ep: Episode, rel: str, *, detail_prefix: str = "") -> Path:
    """把相對 ep.dir 的使用者輸入路徑解析成絕對路徑，擋 ../ 跳脫。

    只負責邊界驗證；存在性檢查（is_file / 404）依各 endpoint 語意自理。
    """
    target = (ep.dir / rel).resolve()
    try:
        target.relative_to(ep.dir.resolve())
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"{detail_prefix}路徑必須在集資料夾內"
        )
    return target


def _normalize_glossary_entries(raw: list) -> list[dict]:
    """整理使用者貼進來的詞庫條目；canonical 為主鍵，去重保留最後一筆。
    支援純字串簡寫（同 config.normalize_glossary）→ {canonical, sounds_like, note}。
    """
    seen: dict[str, dict] = {}
    for item in raw or []:
        if isinstance(item, str):
            c = item.strip()
            if c:
                seen[c] = {"canonical": c, "sounds_like": [], "note": ""}
            continue
        if not isinstance(item, dict):
            continue
        canonical = (item.get("canonical") or "").strip()
        if not canonical:
            continue
        sounds_like_raw = item.get("sounds_like") or []
        sounds_like = []
        if isinstance(sounds_like_raw, list):
            sounds_like = [str(s).strip() for s in sounds_like_raw if str(s).strip()]
        seen[canonical] = {
            "canonical": canonical,
            "sounds_like": sounds_like,
            "note": str(item.get("note", "")),
        }
    return list(seen.values())


def _load_glossary_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return _normalize_glossary_entries(data)


def _save_glossary_file(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_common_glossary() -> list[dict]:
    return _load_glossary_file(COMMON_GLOSSARY_PATH)


def _save_common_glossary(entries: list[dict]) -> None:
    _save_glossary_file(COMMON_GLOSSARY_PATH, entries)


def _episode_glossary_path(ep_dir: Path) -> Path:
    return ep_dir / EPISODE_GLOSSARY_FILENAME


def _load_episode_glossary(ep_dir: Path) -> list[dict]:
    return _load_glossary_file(_episode_glossary_path(ep_dir))


def _save_episode_glossary(ep_dir: Path, entries: list[dict]) -> None:
    _save_glossary_file(_episode_glossary_path(ep_dir), entries)


def _check_assets_status() -> dict:
    """檢查 defaults.yaml 指向的共用資產檔案是否存在。
    給前端 settings 顯示 ✓/✗ 用，避免第一次合成才發現缺檔。
    """
    try:
        defaults = pt_config.load_defaults()
    except Exception:
        return {}
    root = pt_config.toolkit_root()
    out: dict[str, dict] = {}
    for key in ("intro", "outro_audio", "outro_image", "logo"):
        rel = (defaults.get("assets") or {}).get(key)
        if not rel:
            continue
        p = (root / rel).resolve()
        out[key] = {"path": rel, "exists": p.is_file()}
    return out


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
