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
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic
from typing import Callable

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from podcast_toolkit.episode import Episode
from podcast_toolkit.fsutil import atomic_write_text
from podcast_toolkit.web import assemble_job, transcribe_job
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

# ── idle-shutdown 常數 ──────────────────────────────────────────
# 閾值：最後一次心跳後超過這個秒數、且無 active job，就自動 graceful shutdown。
IDLE_SHUTDOWN_SEC = 90

# ── idle-watchdog 防重複啟動 ─────────────────────────────────────
# 全進程只允許一條 watchdog thread；Lock 保護旗標的讀寫。
_watchdog_lock = threading.Lock()
_watchdog_started = False

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


def should_idle_shutdown(
    last_heartbeat_ts: float,
    now_ts: float,
    has_active_job: bool,
    idle_threshold_sec: float,
) -> bool:
    """純函式：判斷是否應觸發 idle shutdown。

    - has_active_job 為 True 時一律不關（轉錄/合成進行中）
    - 距離最後心跳超過 idle_threshold_sec 才關
    """
    if has_active_job:
        return False
    return (now_ts - last_heartbeat_ts) >= idle_threshold_sec


def _start_idle_watchdog(
    get_last_heartbeat: Callable[[], float],
    shutdown: Callable[[], None],
    idle_threshold_sec: float = IDLE_SHUTDOWN_SEC,
    check_interval_sec: float = 30.0,
) -> None:
    """啟動 daemon thread，每 check_interval_sec 秒檢查一次是否閒置過久。

    全進程只啟動一條；重複呼叫（測試多次 build_app）直接 return。
    """
    global _watchdog_started
    with _watchdog_lock:
        if _watchdog_started:
            return
        _watchdog_started = True

    def _watch() -> None:
        while True:
            threading.Event().wait(check_interval_sec)
            has_job = transcribe_job.is_busy() or assemble_job.is_busy()
            if should_idle_shutdown(
                get_last_heartbeat(),
                monotonic(),
                has_job,
                idle_threshold_sec,
            ):
                # 保險：先清理子進程（理論上此時無 active job，但以防萬一）
                try:
                    transcribe_job.cancel_job()
                except Exception:
                    pass
                try:
                    assemble_job.cancel_job()
                except Exception:
                    pass
                shutdown()
                return  # watchdog 退出，server 會停止

    t = threading.Thread(target=_watch, daemon=True, name="idle-watchdog")
    t.start()


def build_app(
    ep: Episode | None,
    shutdown: Callable[[], None],
    *,
    _idle_threshold_sec: float = IDLE_SHUTDOWN_SEC,
    _idle_check_interval_sec: float = 30.0,
) -> FastAPI:
    """建立 FastAPI app。shutdown 是儲存後/取消時呼叫的 callback。

    _idle_threshold_sec / _idle_check_interval_sec：僅供測試注入短值，
    production 固定使用預設值（IDLE_SHUTDOWN_SEC=90, interval=30s）。
    """

    @asynccontextmanager
    async def lifespan(application: FastAPI):  # type: ignore[type-arg]
        # startup：無需額外動作
        yield
        # shutdown：清理可能殘留的子進程（watchdog 觸發 / SIGTERM / Cmd+Q 三條路都會經過）
        try:
            transcribe_job.cancel_job()
        except Exception:
            pass
        try:
            assemble_job.cancel_job()
        except Exception:
            pass

    app = FastAPI(title="podcast-edit", lifespan=lifespan)

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

    # ── idle-shutdown：心跳 endpoint + watchdog ──────────────────
    # 啟動時間作為初始心跳（避免還沒有瀏覽器連線就被關閉）
    # 存在 app.state 讓 endpoint 寫入、watchdog 讀取、測試直接存取都指向同一份。
    app.state.last_heartbeat = monotonic()

    @app.post("/api/heartbeat", status_code=204)
    async def heartbeat() -> None:
        """前端每 20 秒打一次；更新最後活躍時間戳。"""
        app.state.last_heartbeat = monotonic()

    # 把 shutdown callback 傳給 watchdog，讓它在判定閒置後觸發 graceful shutdown
    _start_idle_watchdog(
        get_last_heartbeat=lambda: app.state.last_heartbeat,
        shutdown=shutdown,
        idle_threshold_sec=_idle_threshold_sec,
        check_interval_sec=_idle_check_interval_sec,
    )

    return app
