"""FastAPI app 工廠：給 edit.py 起 server 用。"""
from __future__ import annotations
import threading
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from podcast_toolkit.episode import Episode
from podcast_toolkit.web import episode_io, video


STATIC_DIR = Path(__file__).resolve().parent / "static"


def build_app(ep: Episode, shutdown: Callable[[], None]) -> FastAPI:
    """建立 FastAPI app。shutdown 是儲存後/取消時呼叫的 callback。"""
    app = FastAPI(title="podcast-edit")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/episode")
    def get_episode():
        return JSONResponse(episode_io.load_state(ep))

    @app.get("/api/video")
    def get_video(request: Request):
        return video.range_response(ep.main_video(), request.headers.get("range"))

    @app.post("/api/save")
    def save(payload: dict):
        episode_io.save_state(ep, payload)
        # 延遲呼叫 shutdown,讓 response 先送出
        threading.Timer(0.3, shutdown).start()
        return {"ok": True}

    @app.post("/api/shutdown")
    def cancel():
        threading.Timer(0.3, shutdown).start()
        return Response(status_code=204)

    return app
