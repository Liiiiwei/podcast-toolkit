"""podcast edit：啟動本機 FastAPI + 開瀏覽器 + lockfile。"""
from __future__ import annotations
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app


LOCK_NAME = ".edit.lock"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _acquire_lock(lock_path: Path, port: int) -> bool:
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text(encoding="utf-8").splitlines()[0])
            os.kill(pid, 0)  # 確認 pid 還活著
            return False
        except (ValueError, ProcessLookupError, OSError):
            # 殘留 lockfile,清掉
            try: lock_path.unlink()
            except OSError: pass
    lock_path.write_text(f"{os.getpid()}\n{port}\n", encoding="utf-8")
    return True


def _release_lock(lock_path: Path) -> None:
    try: lock_path.unlink()
    except OSError: pass


def run(episode_dir: Path) -> int:
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

    port = _find_free_port()
    lock_path = ep.subdir("work") / LOCK_NAME
    if not _acquire_lock(lock_path, port):
        print(f"✗ 已有 podcast edit 在跑：{lock_path}", file=sys.stderr)
        return 1

    server = {"instance": None}

    def shutdown_callback():
        if server["instance"] is not None:
            server["instance"].should_exit = True

    app = build_app(ep, shutdown=shutdown_callback)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server["instance"] = uvicorn.Server(config)

    url = f"http://127.0.0.1:{port}"
    print(f"→ 啟動編輯介面：{url}")
    # 延遲 0.5s 開瀏覽器,讓 server 先 ready
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server["instance"].run()
    finally:
        _release_lock(lock_path)

    print("✅ 編輯結束")
    return 0
