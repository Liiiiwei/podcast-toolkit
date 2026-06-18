"""媒體存取：影音預覽（range）、上傳、檔案列表、Finder reveal。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from podcast_toolkit.web import video
from podcast_toolkit.web.shared import (
    AUDIO_EXTS,
    AUDIO_MIME,
    PREVIEWABLE_EXTS,
    TRANSCRIBABLE_EXTS,
    RouteContext,
    _list_episode_files,
    validate_episode_path,
)


def register(app: FastAPI, ctx: RouteContext) -> None:

    @app.get("/api/video")
    def get_video(request: Request, path: str | None = None):
        ep = ctx.require_ep()
        # path 為空 → cam A 正片；否則必須在 ep.dir 內且可預覽
        if not path:
            # cam A 正典來源是 cameras.a（assemble.py 也用它當正片）。main_video 是舊的單機欄位，
            # 多機集可能跟 cameras.a 不一致（曾見 main_video 指到 cam B 檔）→ 預覽 cam A 誤播成 cam B、
            # 跟 cam B overlay 同一支檔，AB 切換看起來「沒切換」。單機集 cameras.a==main_video，無差異。
            cam_a = (ep.cfg.get("cameras") or {}).get("a")
            target = ep.resolve_episode_path(cam_a) if cam_a else ep.main_video()
            # 空集（init 完但 01_母帶/ 沒檔）解析後不存在 → 回 404
            # 不要讓 range_response 的 path.stat() 直接拋 FileNotFoundError 變成 500 噪音
            if not target.is_file():
                raise HTTPException(status_code=404, detail="這集還沒有主影片")
        else:
            target = validate_episode_path(ep, path)
            if not target.is_file():
                raise HTTPException(status_code=404, detail=f"找不到檔案：{path}")
            if target.suffix.lower() not in PREVIEWABLE_EXTS:
                raise HTTPException(status_code=400, detail="不支援預覽的副檔名")
        return video.range_response(target, request.headers.get("range"))

    @app.get("/api/output-video")
    def get_output_video(request: Request):
        """直接串流成品 YT 完整版，不需要帶中文路徑。"""
        ep = ctx.require_ep()
        target = ep.output_yt_video()
        if not target.is_file():
            raise HTTPException(status_code=404, detail="成品不存在，請先渲染")
        return video.range_response(target, request.headers.get("range"))

    @app.get("/api/audio")
    def get_audio(request: Request, path: str):
        ep = ctx.require_ep()
        if not path:
            raise HTTPException(status_code=400, detail="缺少 path")
        target = validate_episode_path(ep, path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"找不到檔案：{path}")
        ext = target.suffix.lower()
        if ext not in AUDIO_EXTS:
            raise HTTPException(status_code=400, detail="不支援預覽的音檔副檔名")
        mime = AUDIO_MIME.get(ext, "audio/mpeg")
        return video.range_response(target, request.headers.get("range"), media_type=mime)

    @app.post("/api/upload")
    async def post_upload(file: UploadFile = File(...)):
        """拖放上傳：把音/影片寫到 01_母帶/。
        檔名只取 basename 防跳脫；副檔名須在 TRANSCRIBABLE_EXTS；同名不覆蓋。"""
        ep = ctx.require_ep()
        raw_name = file.filename or ""
        if not raw_name:
            raise HTTPException(status_code=400, detail="缺少檔名")
        # 防路徑跳脫：含分隔字元的檔名一律 reject（不只取 basename，避免歧義）
        if "/" in raw_name or "\\" in raw_name or raw_name in (".", ".."):
            raise HTTPException(status_code=400, detail="檔名不可包含路徑分隔字元")
        ext = Path(raw_name).suffix.lower()
        if ext not in TRANSCRIBABLE_EXTS:
            raise HTTPException(status_code=400, detail=f"不支援的副檔名：{ext}")

        dest_dir = ep.dir / "01_母帶"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / raw_name
        if dest.exists():
            raise HTTPException(status_code=409, detail=f"已存在同名檔案：{raw_name}")

        # 串流寫入，避免大檔吃光記憶體
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)

        rel = str(dest.relative_to(ep.dir))
        return JSONResponse({"ok": True, "path": rel, "size": dest.stat().st_size})

    @app.get("/api/files")
    def get_files():
        ep = ctx.require_ep()
        return JSONResponse({
            "root": ep.name,
            "dir": str(ep.dir),
            "files": _list_episode_files(ep.dir),
        })

    @app.post("/api/reveal")
    def post_reveal(payload: dict):
        """用 macOS `open` 開資料夾或檔案；路徑必須在 ep.dir 內。"""
        ep = ctx.require_ep()
        raw = (payload.get("path") or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="缺少 path")
        target = Path(raw)
        if not target.is_absolute():
            target = (ep.dir / target).resolve()
        else:
            target = target.resolve()
        try:
            target.relative_to(ep.dir.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail="路徑必須在集資料夾內")
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"找不到：{target}")
        # 若是檔案就用 -R reveal in Finder；資料夾直接開
        cmd = ["open", "-R", str(target)] if target.is_file() else ["open", str(target)]
        try:
            subprocess.run(cmd, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            raise HTTPException(status_code=500, detail=f"無法開啟：{e}")
        return JSONResponse({"ok": True})
