"""podcast edit / podcast ui：啟動本機 FastAPI + 開瀏覽器。"""
from __future__ import annotations
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from podcast_toolkit import server_lock
from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app


LOCK_PATH = Path.home() / ".podcast-toolkit" / ".server.lock"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(ep: Episode | None) -> int:
    """共用 server 啟動邏輯。回傳 exit code。"""
    port = _find_free_port()
    if not server_lock.acquire(LOCK_PATH, port):
        existing = server_lock.read(LOCK_PATH)
        if existing:
            existing_port = existing[1]
            url = f"http://127.0.0.1:{existing_port}"
            print(f"→ 已有 podcast server 在跑，開啟既有 instance：{url}")
            webbrowser.open(url)
            return 0
        print(f"✗ lockfile 異常：{LOCK_PATH}", file=sys.stderr)
        return 1

    server = {"instance": None}

    def shutdown_callback():
        if server["instance"] is not None:
            server["instance"].should_exit = True

    app = build_app(ep, shutdown=shutdown_callback)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server["instance"] = uvicorn.Server(config)

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
