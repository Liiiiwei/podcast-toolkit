"""podcast edit / podcast ui：啟動本機 FastAPI + 開瀏覽器。"""
from __future__ import annotations
import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable

import uvicorn

from podcast_toolkit import server_lock
from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app

try:
    # build_app.sh 每次打包寫入，代表「這個行程正在跑的版本」；開發樹沒有 → "dev"。
    from podcast_toolkit._build_info import BUILD_ID as _BUILD_ID
except ImportError:
    _BUILD_ID = "dev"


LOCK_PATH = Path.home() / ".podcast-toolkit" / ".server.lock"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _probe_build_id(port: int, *, timeout: float = 2.0) -> str | None:
    """HTTP 探既有 server 的記憶體版本（/api/version）。

    連不上／無此 endpoint（舊後端回 404）／回應非預期一律回 None
    —— 呼叫端會把 None 當「需要接管的舊行程」。"""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/version", timeout=timeout
        ) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    bid = data.get("build_id")
    return bid if isinstance(bid, str) else None


def _should_reuse(running_build_id: str | None, my_build_id: str) -> bool:
    """既有 server 版本與本次啟動「相同」才沿用；探不到或版本不同都要接管。"""
    return running_build_id is not None and running_build_id == my_build_id


def _terminate_and_wait(pid: int, *, timeout: float = 10.0) -> bool:
    """SIGTERM 既有行程並等它讓出 server lock。

    舊行程收到 SIGTERM 會 graceful shutdown 並在 finally 釋放 lock。
    行程消失或 lock 被釋放即算讓出成功。逾時未讓出回 False。"""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        # 行程早就不在 → lock 是殘檔，清掉即可
        server_lock.release(LOCK_PATH)
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True                 # 行程已結束
        if not LOCK_PATH.exists():
            return True                 # lock 已被舊行程 release
        time.sleep(0.2)
    return False


def _handle_existing_server(
    existing_pid: int,
    existing_port: int,
    *,
    probe: Callable[[int], "str | None"] = _probe_build_id,
    terminate: Callable[[int], bool] = _terminate_and_wait,
    open_browser: Callable[[str], object] = webbrowser.open,
) -> int | None:
    """lock 已被占用時的決策（副作用函式可注入，便於測試）。

    回傳 int＝呼叫端應立即 return 的 exit code（沿用既有 / 接管失敗）；
    回傳 None＝已終止舊行程並讓出 lock，呼叫端應繼續啟動新 server。"""
    running = probe(existing_port)
    if _should_reuse(running, _BUILD_ID):
        url = f"http://127.0.0.1:{existing_port}"
        print(f"→ 已有相同版本的 podcast server 在跑，開啟既有 instance：{url}")
        open_browser(url)
        return 0
    # 版本不符或探不到 → 殘留的舊版行程。若直接沿用，新裝的 App 會被導去舊行程，
    # 前端偵測到記憶體版本落後磁碟 → 誤報「執行的是舊版本」橫幅，舊行程又會在
    # 90 秒閒置後自動關閉。改為關掉它、搶回 lock，用目前版本重起。
    print(
        f"→ 偵測到殘留的舊版 server（build={running or '未知'}），"
        f"關閉它並改用目前版本（build={_BUILD_ID}）…"
    )
    if not terminate(existing_pid):
        print(
            "✗ 舊版 server 未能在時限內結束；請按 ⌘Q 完全結束 App 後再重開。",
            file=sys.stderr,
        )
        return 1
    return None


def _start_server(ep: Episode | None) -> int:
    """共用 server 啟動邏輯。回傳 exit code。"""
    port = _find_free_port()
    if not server_lock.acquire(LOCK_PATH, port):
        existing = server_lock.read(LOCK_PATH)
        if not existing:
            print(f"✗ lockfile 異常：{LOCK_PATH}", file=sys.stderr)
            return 1
        outcome = _handle_existing_server(existing[0], existing[1])
        if outcome is not None:
            return outcome
        # 已接管：舊行程讓出 lock，用新的 free port 重取
        port = _find_free_port()
        if not server_lock.acquire(LOCK_PATH, port):
            print(f"✗ 接管舊 server 後仍無法取得 lock：{LOCK_PATH}", file=sys.stderr)
            return 1

    server = {"instance": None}

    def shutdown_callback():
        if server["instance"] is not None:
            server["instance"].should_exit = True

    app = build_app(ep, shutdown=shutdown_callback)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server["instance"] = uvicorn.Server(config)

    # SIGTERM / SIGINT handler：只設 should_exit=True，讓 uvicorn 事件迴圈自然結束。
    # 子進程清理已移入 FastAPI lifespan shutdown hook（api.py），
    # uvicorn 停止時必然執行那段，不再需要在 signal handler 裡重複清理，
    # 避免與 uvicorn 自身的 capture_signals 打架。
    # SIGKILL（-9）攔不了，OS 會回收子進程，無法事先清理。
    def _signal_handler(signum: int, frame) -> None:  # type: ignore[type-arg]
        shutdown_callback()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    url = f"http://127.0.0.1:{port}"
    print(f"→ 啟動：{url}")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server["instance"].run()
    finally:
        server_lock.release(LOCK_PATH)
    print("✅ server 已停止")
    return 0


def run_with_ep(episode_dir: Path) -> int:
    """podcast edit <path> — 直接帶集數進 edit 模式。"""
    ep = Episode(episode_dir)
    main_video = ep.main_video()
    if not main_video.exists():
        print(f"✗ main_video 缺失：{main_video}", file=sys.stderr)
        return 3
    v2 = ep.output_v2_srt()
    if not v2.exists():
        print(f"✗ 找不到 _v2.srt：{v2}", file=sys.stderr)
        print(f"  請先跑 podcast resegment {episode_dir}", file=sys.stderr)
        return 3
    return _start_server(ep)


def run_dashboard() -> int:
    """podcast ui — 進 dashboard 模式（無預選集）。"""
    return _start_server(None)


# 兼容舊 entry：cli.py 的 cmd_edit 還在呼叫 edit.run(path)
def run(episode_dir: Path) -> int:
    return run_with_ep(episode_dir)
