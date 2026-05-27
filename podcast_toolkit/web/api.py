"""FastAPI app 工廠：給 edit.py 起 server 用。"""
from __future__ import annotations
import json
import threading
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from podcast_toolkit.episode import Episode
from podcast_toolkit.web import episode_io, video


STATIC_DIR = Path(__file__).resolve().parent / "static"
TYPO_DICT_PATH = Path.home() / ".podcast-toolkit" / "typo-dict.json"


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

    @app.get("/api/typo-dict")
    def get_typo_dict():
        return JSONResponse(_load_typo_dict())

    @app.post("/api/typo-dict")
    def post_typo_dict(payload: dict):
        # payload = {"entries": [{"wrong": "...", "right": "...", "note": "..."}]}
        # 整批覆寫（前端先 GET → 編 → POST）。去重以 wrong 為 key，保留最後一筆
        raw = payload.get("entries") or []
        seen: dict[str, dict] = {}
        for e in raw:
            if not isinstance(e, dict):
                continue
            w, r = e.get("wrong"), e.get("right")
            if not w or not r:
                continue
            seen[str(w)] = {
                "wrong": str(w),
                "right": str(r),
                "note": str(e.get("note", "")),
            }
        entries = list(seen.values())
        _save_typo_dict(entries)
        return JSONResponse(entries)

    return app
