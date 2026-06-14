"""合成：背景 assemble job、狀態、Reels 快速切片。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from podcast_toolkit.assemble import AssembleError
from podcast_toolkit.web import assemble_job
from podcast_toolkit.web.shared import RouteContext


def register(app: FastAPI, ctx: RouteContext) -> None:

    @app.post("/api/assemble")
    def post_assemble(payload: dict):
        ep = ctx.require_ep()
        targets = payload.get("targets") or []
        if not targets or not isinstance(targets, list):
            raise HTTPException(status_code=400, detail="缺少 targets（list，例如 ['yt', 'reels']）")
        force = bool(payload.get("force"))
        preview_sec_raw = payload.get("preview_sec")
        preview_sec: int | None = None
        if preview_sec_raw is not None:
            try:
                preview_sec = int(preview_sec_raw)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="preview_sec 必須是正整數")
            if preview_sec <= 0:
                preview_sec = None
        # subtitle_mode：burn=字幕燒進畫面（預設）、sidecar=影片不燒+另存對齊 .srt
        subtitle_mode = payload.get("subtitle_mode") or "burn"
        if subtitle_mode not in ("burn", "sidecar"):
            raise HTTPException(status_code=400, detail="subtitle_mode 必須是 burn 或 sidecar")
        try:
            info = assemble_job.start_job(
                ep, targets=targets, force=force, preview_sec=preview_sec,
                subtitle_mode=subtitle_mode,
            )
        except AssembleError as e:
            # 資產缺失 / 輸出存在 / 找不到 ffmpeg
            # 注意：AssembleError 繼承 RuntimeError，必須先攔
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError as e:
            # 已有 job 在跑
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return JSONResponse({
            "ok": True,
            "targets": info["targets"],
            "out_paths": info["out_paths"],
        })

    @app.get("/api/assemble/status")
    def get_assemble_status():
        return JSONResponse(assemble_job.get_status())

    @app.post("/api/clip")
    def post_clip(payload: dict):
        """同步切 Reels 片段（-c copy 很快，沒必要 background job）。
        payload: { names?: list[str], force?: bool }
        names 省略 = 跑全部；給 list = 只跑指定 name。"""
        from podcast_toolkit.assemble import extract_reels_clips

        ep = ctx.require_ep()
        names = payload.get("names")
        if names is not None and not isinstance(names, list):
            raise HTTPException(status_code=400, detail="names 必須是 list 或省略")
        force = bool(payload.get("force"))
        try:
            results = extract_reels_clips(
                ep.dir,
                clip_names=list(names) if names else None,
                force=force,
            )
        except AssembleError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail=f"ffmpeg 失敗：exit {e.returncode}")
        # 路徑轉相對 ep.dir 給前端 reveal/preview
        out = []
        for r in results:
            rel = Path(r["path"])
            try:
                rel = rel.relative_to(ep.dir)
            except ValueError:
                pass
            out.append({
                "name": r["name"],
                "duration": round(float(r["duration"]), 2),
                "start_sec": round(float(r["start_sec"]), 2),
                "end_sec": round(float(r["end_sec"]), 2),
                "path": str(rel),
            })
        return JSONResponse({"ok": True, "clips": out})
