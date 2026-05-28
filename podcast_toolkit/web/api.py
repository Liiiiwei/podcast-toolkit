"""FastAPI app 工廠：給 edit.py 起 server 用。"""
from __future__ import annotations
import json
import os
import threading
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from podcast_toolkit.episode import Episode
from podcast_toolkit.web import episode_io, transcribe, video


STATIC_DIR = Path(__file__).resolve().parent / "static"
CONFIG_DIR = Path.home() / ".podcast-toolkit"
TYPO_DICT_PATH = CONFIG_DIR / "typo-dict.json"
CONFIG_PATH = CONFIG_DIR / "config.json"

# 可轉字幕的副檔名（含音訊與含音訊軌的影片）
TRANSCRIBABLE_EXTS = {
    ".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus",
    ".mp4", ".mov", ".mkv", ".webm",
}
# 可在瀏覽器直接預覽的影片副檔名
PREVIEWABLE_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
# 列檔時忽略的目錄/檔名片段
SKIP_DIRS = {".DS_Store", "__pycache__", ".git"}


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


def _list_episode_files(root: Path) -> list[dict]:
    """遞迴列出集資料夾內所有檔案，標註是否可轉字幕。"""
    files: list[dict] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        # 過濾隱藏 / 快取目錄
        if any(part in SKIP_DIRS or part.startswith(".") for part in p.relative_to(root).parts):
            continue
        rel = str(p.relative_to(root))
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        files.append({
            "path": rel,
            "size": size,
            "transcribable": p.suffix.lower() in TRANSCRIBABLE_EXTS,
            "previewable": p.suffix.lower() in PREVIEWABLE_EXTS,
        })
    return files


def build_app(ep: Episode, shutdown: Callable[[], None]) -> FastAPI:
    """建立 FastAPI app。shutdown 是儲存後/取消時呼叫的 callback。"""
    app = FastAPI(title="podcast-edit")
    # 用 dict 包住，讓 /api/episode/switch 能 hot-swap
    holder = {"ep": ep}

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/episode")
    def get_episode():
        return JSONResponse(episode_io.load_state(holder["ep"]))

    @app.post("/api/episode/switch")
    def switch_episode(payload: dict):
        raw = (payload.get("path") or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="缺少 path")
        new_dir = Path(os.path.expanduser(raw)).resolve()
        if not new_dir.is_dir():
            raise HTTPException(status_code=400, detail=f"資料夾不存在：{new_dir}")
        if not (new_dir / "episode.yaml").is_file():
            raise HTTPException(
                status_code=400,
                detail=f"不是 episode 資料夾（缺 episode.yaml）：{new_dir}",
            )
        try:
            new_ep = Episode(new_dir)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"無法載入 episode：{e}")
        holder["ep"] = new_ep
        return JSONResponse({
            "ok": True,
            "name": new_ep.name,
            "dir": str(new_ep.dir),
        })

    @app.get("/api/video")
    def get_video(request: Request, path: str | None = None):
        ep = holder["ep"]
        # path 為空 → main_video；否則必須在 ep.dir 內且可預覽
        if not path:
            target = ep.main_video()
        else:
            target = (ep.dir / path).resolve()
            try:
                target.relative_to(ep.dir)
            except ValueError:
                raise HTTPException(status_code=400, detail="路徑必須在集資料夾內")
            if not target.is_file():
                raise HTTPException(status_code=404, detail=f"找不到檔案：{path}")
            if target.suffix.lower() not in PREVIEWABLE_EXTS:
                raise HTTPException(status_code=400, detail="不支援預覽的副檔名")
        return video.range_response(target, request.headers.get("range"))

    @app.post("/api/save")
    def save(payload: dict):
        episode_io.save_state(holder["ep"], payload)
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

    @app.get("/api/files")
    def get_files():
        ep = holder["ep"]
        return JSONResponse({
            "root": ep.name,
            "dir": str(ep.dir),
            "files": _list_episode_files(ep.dir),
        })

    @app.get("/api/config")
    def get_config():
        cfg = _load_config()
        # 隱藏實際 key，前端只要知道有沒有設定
        return JSONResponse({
            "has_xai_api_key": bool(cfg.get("xai_api_key")),
        })

    @app.post("/api/config")
    def post_config(payload: dict):
        cfg = _load_config()
        if "xai_api_key" in payload:
            key = (payload.get("xai_api_key") or "").strip()
            if key:
                cfg["xai_api_key"] = key
            else:
                cfg.pop("xai_api_key", None)
        _save_config(cfg)
        return JSONResponse({"has_xai_api_key": bool(cfg.get("xai_api_key"))})

    @app.post("/api/transcribe")
    def post_transcribe(payload: dict):
        ep = holder["ep"]
        rel = (payload.get("path") or "").strip()
        if not rel:
            raise HTTPException(status_code=400, detail="缺少 path")

        # 防止路徑跳脫
        src = (ep.dir / rel).resolve()
        try:
            src.relative_to(ep.dir)
        except ValueError:
            raise HTTPException(status_code=400, detail="路徑必須在集資料夾內")
        if not src.is_file():
            raise HTTPException(status_code=404, detail=f"找不到檔案：{rel}")
        if src.suffix.lower() not in TRANSCRIBABLE_EXTS:
            raise HTTPException(status_code=400, detail="不支援的副檔名")

        cfg = _load_config()
        api_key = cfg.get("xai_api_key")
        if not api_key:
            raise HTTPException(status_code=400, detail="尚未設定 xAI API key")

        try:
            out_srt = transcribe.run_grok_pipeline(
                api_key=api_key,
                src_audio=src,
                out_srt=ep.output_v2_srt(),
                work_dir=ep.subdir("work"),
            )
        except transcribe.TranscribeError as e:
            raise HTTPException(status_code=502, detail=str(e))

        return JSONResponse({
            "ok": True,
            "out_srt": str(out_srt.relative_to(ep.dir)),
        })

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
