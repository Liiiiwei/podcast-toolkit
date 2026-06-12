"""FastAPI app 工廠：給 edit.py 起 server 用。

路由實作在 web/routes/*（每個檔案一個領域），共用 helper 在 web/shared.py。
config.json / typo-dict.json 的存取 helper 留在這個模組——測試會
monkeypatch 這裡的 `_load_config` / `_save_config` / `_load_typo_dict` /
`CONFIG_PATH`，build_app 在呼叫當下把引用打包進 RouteContext，
所以 patch 必須在 build_app 之前做。
"""
from __future__ import annotations

import json
import subprocess  # noqa: F401  (測試 monkeypatch api_mod.subprocess.run)
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from podcast_toolkit.episode import Episode
from podcast_toolkit.web.routes import (
    assemble as assemble_routes,
)
from podcast_toolkit.web.routes import (
    config as config_routes,
)
from podcast_toolkit.web.routes import (
    editor as editor_routes,
)
from podcast_toolkit.web.routes import (
    episodes as episodes_routes,
)
from podcast_toolkit.web.routes import (
    media as media_routes,
)
from podcast_toolkit.web.routes import (
    transcribe as transcribe_routes,
)
from podcast_toolkit.web.shared import (  # noqa: F401  (向後相容 re-export)
    AUDIO_EXTS,
    AUDIO_MIME,
    COMMON_GLOSSARY_PATH,
    EPISODE_GLOSSARY_FILENAME,
    PREVIEWABLE_EXTS,
    SKIP_DIRS,
    STATIC_DIR,
    TRANSCRIBABLE_EXTS,
    RouteContext,
    _check_assets_status,
    _list_episode_files,
    _load_common_glossary,
    _load_episode_glossary,
    _normalize_glossary_entries,
    _save_common_glossary,
    _save_episode_glossary,
)

CONFIG_DIR = Path.home() / ".podcast-toolkit"
TYPO_DICT_PATH = CONFIG_DIR / "typo-dict.json"
CONFIG_PATH = CONFIG_DIR / "config.json"


def _load_typo_dict() -> list[dict]:
    if not TYPO_DICT_PATH.exists():
        return []
    try:
        data = json.loads(TYPO_DICT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    # 兼容性過濾：只接受 {wrong, right} 結構
    return [
        {"wrong": str(e["wrong"]), "right": str(e["right"]),
         "note": str(e.get("note", ""))}
        for e in data
        if isinstance(e, dict) and e.get("wrong") and e.get("right")
    ]


def _save_typo_dict(entries: list[dict]) -> None:
    TYPO_DICT_PATH.parent.mkdir(parents=True, exist_ok=True)
    TYPO_DICT_PATH.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_app(ep: Episode | None, shutdown: Callable[[], None]) -> FastAPI:
    """建立 FastAPI app。shutdown 是儲存後/取消時呼叫的 callback。"""
    app = FastAPI(title="podcast-edit")
    ctx = RouteContext(
        # 用 dict 包住，讓 /api/episode/switch 能 hot-swap
        holder={"ep": ep},
        shutdown=shutdown,
        # 包 lambda 做 late-bound：路由呼叫當下才查本模組 global，
        # 測試 monkeypatch api 模組（不論在 build_app 前或後）都會生效
        load_config=lambda: _load_config(),
        save_config=lambda data: _save_config(data),
        load_typo_dict=lambda: _load_typo_dict(),
        save_typo_dict=lambda entries: _save_typo_dict(entries),
        get_config_path=lambda: CONFIG_PATH,
    )

    episodes_routes.register(app, ctx)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    media_routes.register(app, ctx)
    editor_routes.register(app, ctx)
    transcribe_routes.register(app, ctx)
    assemble_routes.register(app, ctx)
    config_routes.register(app, ctx)

    return app
