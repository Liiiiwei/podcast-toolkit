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
import sys
import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from podcast_toolkit.episode import Episode
from podcast_toolkit.fsutil import atomic_write_text
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
from podcast_toolkit.web.shared import (
    STATIC_DIR,
    RouteContext,
    probe_static_access,
)

class NoCacheStaticFiles(StaticFiles):
    """靜態檔一律回 Cache-Control: no-cache，廢除手動 ?v= 撞號。

    no-cache ≠ 不快取：瀏覽器每次都會帶 If-None-Match/If-Modified-Since
    回來 revalidate，檔案沒變就吃 304（StaticFiles 內建 ETag/Last-Modified
    條件請求）。本地 app 走 localhost，這個成本近零，換到「改了前端
    永遠立刻生效」，不再發生版號漏撞導致使用者跑舊版 JS。
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        # 200 與 304 都要帶，否則 304 之後瀏覽器可能改用啟發式快取
        response.headers["Cache-Control"] = "no-cache"
        return response


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
    atomic_write_text(
        TYPO_DICT_PATH,
        json.dumps(entries, ensure_ascii=False, indent=2),
    )


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # 壞 JSON 不能靜默歸零：先把壞檔搬走保留現場（也許能手救），
        # 否則使用者下次隨手存個設定就把原檔永久蓋掉。
        ts = time.strftime("%Y%m%d-%H%M%S")
        corrupt = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.corrupt-{ts}")
        try:
            CONFIG_PATH.replace(corrupt)
            print(
                f"[podcast-toolkit] 警告：{CONFIG_PATH.name} 解析失敗，"
                f"已備份成 {corrupt.name} 並重置設定",
                file=sys.stderr,
                flush=True,
            )
        except OSError:
            pass
        return {}
    except OSError:
        # 讀不到（權限等）≠ 檔案壞掉，不搬檔，僅回空設定
        return {}


def _save_config(data: dict) -> None:
    atomic_write_text(
        CONFIG_PATH,
        json.dumps(data, ensure_ascii=False, indent=2),
    )


def build_app(ep: Episode | None, shutdown: Callable[[], None]) -> FastAPI:
    """建立 FastAPI app。shutdown 是儲存後/取消時呼叫的 callback。"""
    app = FastAPI(title="podcast-edit")

    # macOS TCC 預檢：toolkit 裝在受保護資料夾時靜態檔 open 會被擋、整頁空白。
    # 啟動時探一次，結果存進 app.state 給 "/" 路由改回明確錯誤頁（FileResponse
    # 此時也讀不到 body，錯誤頁必須走 inline HTMLResponse，見 routes/episodes.py）。
    tcc_blocked_dir = probe_static_access()
    app.state.tcc_blocked_dir = tcc_blocked_dir
    if tcc_blocked_dir:
        print(
            "[podcast-toolkit] 警告：macOS 權限（TCC）擋住讀取靜態檔："
            f"{tcc_blocked_dir}\n"
            "  → 編輯器會整頁空白。請把 toolkit 安裝目錄移出 ~/Desktop / "
            "~/Downloads / ~/Documents（例如 ~/podcast-toolkit），\n"
            "    或到「系統設定 › 隱私權與安全性 › 完全取用磁碟」把執行的 "
            "Python／終端機加入後重啟。",
            file=sys.stderr,
            flush=True,
        )
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
    app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")
    media_routes.register(app, ctx)
    editor_routes.register(app, ctx)
    transcribe_routes.register(app, ctx)
    assemble_routes.register(app, ctx)
    config_routes.register(app, ctx)

    return app
