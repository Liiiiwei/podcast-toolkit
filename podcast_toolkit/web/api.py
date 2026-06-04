"""FastAPI app 工廠：給 edit.py 起 server 用。"""
from __future__ import annotations
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from podcast_toolkit import audio_align
from podcast_toolkit import init as ep_init
from podcast_toolkit.assemble import AssembleError
from podcast_toolkit.episode import Episode
from podcast_toolkit.web import (
    assemble_job,
    episode_io,
    silencedetect,
    transcribe,
    transcribe_job,
    video,
)
from podcast_toolkit.web import dashboard as dashboard_mod


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
    """遞迴列出集資料夾內所有檔案，標註 kind / 字幕角色。"""
    files: list[dict] = []
    try:
        ep = Episode(root)
        main_video_path = ep.main_video()
        main_srt_path = ep.main_srt()
        v2_srt_path = ep.output_v2_srt()
        yt_out = ep.output_yt_video()
        reels_out = ep.output_reels_video()
    except Exception:
        main_video_path = main_srt_path = v2_srt_path = None
        yt_out = reels_out = None

    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS or part.startswith(".") for part in p.relative_to(root).parts):
            continue
        rel = str(p.relative_to(root))
        try:
            size = p.stat().st_size
        except OSError:
            size = 0

        first = p.relative_to(root).parts[0] if p.relative_to(root).parts else ""
        kind = "other"
        is_active_srt = False
        is_main_srt_backup = False

        if main_video_path and p == main_video_path:
            kind = "main_video"
        elif v2_srt_path and p == v2_srt_path:
            kind = "subtitle"
            is_active_srt = True
        elif main_srt_path and p == main_srt_path:
            kind = "subtitle"
            is_main_srt_backup = True
        elif (yt_out and p == yt_out) or (reels_out and p == reels_out):
            kind = "composite"
        elif p.suffix.lower() == ".srt":
            kind = "subtitle"
        elif first == "01_母帶":
            kind = "master"
        elif first == "04_工作檔":
            kind = "work"

        files.append({
            "path": rel,
            "size": size,
            "transcribable": p.suffix.lower() in TRANSCRIBABLE_EXTS,
            "previewable": p.suffix.lower() in PREVIEWABLE_EXTS,
            "kind": kind,
            "is_active_srt": is_active_srt,
            "is_main_srt_backup": is_main_srt_backup,
        })
    return files


def build_app(ep: Episode | None, shutdown: Callable[[], None]) -> FastAPI:
    """建立 FastAPI app。shutdown 是儲存後/取消時呼叫的 callback。"""
    app = FastAPI(title="podcast-edit")
    # 用 dict 包住，讓 /api/episode/switch 能 hot-swap
    holder = {"ep": ep}

    def _require_ep() -> Episode:
        ep = holder["ep"]
        if ep is None:
            raise HTTPException(status_code=409, detail="尚未選集，請先在 Dashboard 選一集")
        return ep

    @app.get("/")
    def index():
        # 同一個 URL 會依 holder 狀態回 dashboard.html 或 index.html，
        # 沒設 no-store 的話 Chromium 會用啟發式快取直接吃 cache 不打 server，
        # 導致 open 完 redirect 回 / 還是看到舊頁
        headers = {"Cache-Control": "no-store"}
        if holder["ep"] is None:
            return FileResponse(STATIC_DIR / "dashboard.html", headers=headers)
        return FileResponse(STATIC_DIR / "index.html", headers=headers)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/episodes")
    def list_episodes():
        cfg = _load_config()
        roots = cfg.get("episode_roots") or [str(Path.home() / "Downloads")]
        recent = dashboard_mod.load_recent(CONFIG_PATH)
        return JSONResponse(dashboard_mod.list_episodes(roots=roots, recent=recent))

    @app.post("/api/episodes/open")
    def open_episode(payload: dict):
        raw = (payload.get("path") or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="缺少 path")
        target = Path(os.path.expanduser(raw)).resolve()
        if not target.is_dir():
            raise HTTPException(status_code=400, detail=f"資料夾不存在：{target}")
        if not (target / "episode.yaml").is_file():
            raise HTTPException(status_code=400, detail=f"不是 episode 資料夾：{target}")
        try:
            new_ep = Episode(target)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"無法載入 episode：{e}")
        holder["ep"] = new_ep
        dashboard_mod.add_recent(CONFIG_PATH, str(target))
        return JSONResponse({"ok": True})

    @app.post("/api/episodes/close")
    def close_episode():
        holder["ep"] = None
        return JSONResponse({"ok": True})

    @app.get("/api/episode")
    def get_episode():
        return JSONResponse(episode_io.load_state(_require_ep()))

    @app.post("/api/episode/pick")
    def pick_episode():
        ep = holder["ep"]
        default_dir = str(ep.dir.parent) if ep else str(Path.home() / "Downloads")
        script = (
            f'POSIX path of (choose folder with prompt "選擇集資料夾" '
            f'default location POSIX file "{default_dir}")'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="找不到 osascript（需 macOS）")
        except subprocess.TimeoutExpired:
            return JSONResponse({"path": None, "cancelled": True})
        if result.returncode != 0:
            # 使用者取消或其他失敗
            return JSONResponse({"path": None, "cancelled": True})
        picked = result.stdout.strip().rstrip("/")
        if not picked:
            return JSONResponse({"path": None, "cancelled": True})
        return JSONResponse({"path": picked, "cancelled": False})

    @app.post("/api/episode/preview")
    def preview_episode(payload: dict):
        """預覽資料夾內容（給沒 episode.yaml 的資料夾用）。"""
        raw = (payload.get("path") or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="缺少 path")
        target = Path(os.path.expanduser(raw)).resolve()
        if not target.is_dir():
            raise HTTPException(status_code=400, detail=f"資料夾不存在：{target}")
        entries: list[dict] = []
        try:
            for child in sorted(target.iterdir()):
                if child.name in SKIP_DIRS or child.name.startswith("."):
                    continue
                entries.append({
                    "name": child.name,
                    "is_dir": child.is_dir(),
                })
        except PermissionError:
            raise HTTPException(status_code=403, detail=f"沒有權限讀取：{target}")
        date, name = ep_init.parse_folder_name(target)
        return JSONResponse({
            "path": str(target),
            "folder_name": target.name,
            "has_episode_yaml": (target / "episode.yaml").is_file(),
            "matches_convention": bool(date),
            "parsed_date": date or "",
            "parsed_name": name or "",
            "subdirs_to_create": ep_init.SUBDIRS,
            "entries": entries,
        })

    @app.post("/api/episode/init")
    def init_episode(payload: dict):
        """對指定資料夾跑 podcast init（建子目錄 / symlink / template）。"""
        raw = (payload.get("path") or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="缺少 path")
        target = Path(os.path.expanduser(raw)).resolve()
        if not target.is_dir():
            raise HTTPException(status_code=400, detail=f"資料夾不存在：{target}")
        try:
            code = ep_init.run(target)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"init 失敗：{e}")
        if code != 0:
            raise HTTPException(status_code=500, detail=f"init 回傳非 0：{code}")
        return JSONResponse({"ok": True, "path": str(target)})

    @app.post("/api/episode/new")
    def new_episode(payload: dict):
        """在當前集的父資料夾下建 '{date} {name}' 新集 + init + switch。"""
        date = (payload.get("date") or "").strip()
        name = (payload.get("name") or "").strip()
        if not date:
            raise HTTPException(status_code=400, detail="缺少 date")
        if not name:
            raise HTTPException(status_code=400, detail="缺少 name")
        if not (len(date) == 8 and date.isdigit()):
            raise HTTPException(
                status_code=400,
                detail=f"日期格式錯（要 YYYYMMDD）：{date}",
            )
        if "/" in name or "\\" in name or name in (".", ".."):
            raise HTTPException(
                status_code=400,
                detail=f"集名不可包含路徑分隔字元：{name}",
            )
        ep = holder["ep"]
        parent = ep.dir.parent if ep else (Path.home() / "Downloads")
        target = (parent / f"{date} {name}").resolve()
        if target.exists():
            raise HTTPException(
                status_code=409,
                detail=f"已存在同名資料夾：{target.name}",
            )
        target.mkdir(parents=True)
        try:
            code = ep_init.run(target)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"init 失敗：{e}")
        if code != 0:
            raise HTTPException(status_code=500, detail=f"init 回傳非 0：{code}")
        try:
            new_ep = Episode(target)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"無法載入新集：{e}")
        holder["ep"] = new_ep
        return JSONResponse({
            "ok": True,
            "path": str(target),
            "name": new_ep.name,
        })

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
        ep = _require_ep()
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
        ep = _require_ep()
        episode_io.save_state(ep, payload)
        # 重新 init Episode 讓 cfg 反映剛寫入的 yaml；否則 GET /api/episode
        # 還是拿 build_app 當下 cache 的 cfg，A/B toggle 等依賴 refetch 的 UI 不會更新
        holder["ep"] = Episode(ep.dir)
        return {"ok": True}

    @app.post("/api/shutdown")
    def cancel():
        threading.Timer(0.3, shutdown).start()
        return Response(status_code=204)

    @app.get("/api/typo-dict")
    def get_typo_dict():
        return JSONResponse(_load_typo_dict())

    @app.post("/api/upload")
    async def post_upload(file: UploadFile = File(...)):
        """拖放上傳：把音/影片寫到 01_母帶/。
        檔名只取 basename 防跳脫；副檔名須在 TRANSCRIBABLE_EXTS；同名不覆蓋。"""
        ep = _require_ep()
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
        ep = _require_ep()
        return JSONResponse({
            "root": ep.name,
            "dir": str(ep.dir),
            "files": _list_episode_files(ep.dir),
        })

    @app.get("/api/config")
    def get_config():
        cfg = _load_config()
        provider = (cfg.get("transcribe") or {}).get("provider") or "xai"
        if provider not in transcribe.PROVIDERS:
            provider = "xai"
        return JSONResponse({
            "has_xai_api_key": bool(cfg.get("xai_api_key")),
            "has_gemini_api_key": bool(cfg.get("gemini_api_key")),
            "provider": provider,
            "episode_roots": cfg.get("episode_roots") or [str(Path.home() / "Downloads")],
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
        if "gemini_api_key" in payload:
            key = (payload.get("gemini_api_key") or "").strip()
            if key:
                cfg["gemini_api_key"] = key
            else:
                cfg.pop("gemini_api_key", None)
        if "provider" in payload:
            provider = (payload.get("provider") or "").strip()
            if provider not in transcribe.PROVIDERS:
                raise HTTPException(
                    status_code=400, detail=f"未知的 STT 供應商：{provider}"
                )
            tcfg = cfg.get("transcribe") or {}
            tcfg["provider"] = provider
            cfg["transcribe"] = tcfg
        if "episode_roots" in payload:
            roots = payload.get("episode_roots")
            if not isinstance(roots, list) or not all(isinstance(x, str) for x in roots):
                raise HTTPException(status_code=400, detail="episode_roots 必須是字串陣列")
            cfg["episode_roots"] = [r.strip() for r in roots if r.strip()]
        _save_config(cfg)
        out_provider = (cfg.get("transcribe") or {}).get("provider") or "xai"
        return JSONResponse({
            "has_xai_api_key": bool(cfg.get("xai_api_key")),
            "has_gemini_api_key": bool(cfg.get("gemini_api_key")),
            "provider": out_provider,
            "episode_roots": cfg.get("episode_roots") or [str(Path.home() / "Downloads")],
        })

    @app.post("/api/transcribe")
    def post_transcribe(payload: dict):
        """非同步：立即回 202，背景跑壓縮 + Grok + resegment。
        前端 poll /api/transcribe/status 拿進度。"""
        ep = _require_ep()
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
        provider = (cfg.get("transcribe") or {}).get("provider") or "xai"
        if provider not in transcribe.PROVIDERS:
            raise HTTPException(
                status_code=400, detail=f"未知的 STT 供應商：{provider}"
            )
        key_map = {"xai": "xai_api_key", "gemini": "gemini_api_key"}
        label_map = {"xai": "xAI", "gemini": "Gemini"}
        api_key = cfg.get(key_map[provider])
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail=f"尚未設定 {label_map[provider]} API key",
            )

        try:
            info = transcribe_job.start_job(
                ep,
                src_rel=rel,
                provider=provider,
                api_key=api_key,
                typo_entries=_load_typo_dict(),
            )
        except RuntimeError as e:
            # 已有 job 在跑
            raise HTTPException(status_code=409, detail=str(e))

        return JSONResponse(
            {"ok": True, "src_path": info["src_path"]},
            status_code=202,
        )

    @app.get("/api/transcribe/status")
    def get_transcribe_status():
        return JSONResponse(transcribe_job.get_status())

    @app.post("/api/detect-silence")
    def post_detect_silence():
        """智慧建議：跑 ffmpeg silencedetect 看 main_video 開頭靜音長度（秒）。"""
        ep = _require_ep()
        main = ep.main_video()
        if not main.is_file():
            raise HTTPException(
                status_code=400,
                detail=f"找不到 main_video：{main.relative_to(ep.dir)}",
            )
        try:
            head_sec = silencedetect.detect_head_silence(main)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        return JSONResponse({"head_silence_sec": head_sec})

    @app.post("/api/assemble")
    def post_assemble(payload: dict):
        ep = _require_ep()
        targets = payload.get("targets") or []
        if not targets or not isinstance(targets, list):
            raise HTTPException(status_code=400, detail="缺少 targets（list，例如 ['yt', 'reels']）")
        force = bool(payload.get("force"))
        try:
            info = assemble_job.start_job(ep, targets=targets, force=force)
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

    @app.post("/api/reveal")
    def post_reveal(payload: dict):
        """用 macOS `open` 開資料夾或檔案；路徑必須在 ep.dir 內。"""
        ep = _require_ep()
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

    @app.post("/api/auto-align")
    def auto_align_route(payload: dict | None = None):
        """T23b：前 120 秒做音訊互相關，回傳「對齊對象 相對 cam A」的秒偏移。
        不寫 yaml — 前端拿到值填到 input，使用者按儲存才走 /api/save。

        payload 接兩種對齊模式：
        - {"audio_path": "..."}：對「外接音檔 vs cam A」算偏移
        - 否則：對「cam B vs cam A」；cam B 優先讀 payload['cam_b_path']，
          沒給才 fallback 讀 yaml 裡已存的 cameras.b
        """
        ep = _require_ep()
        payload = payload or {}
        cameras = ep.cfg.get("cameras") or {}
        cam_a_rel = cameras.get("a") or ep.cfg.get("main_video")
        if not cam_a_rel:
            raise HTTPException(status_code=400, detail="缺 cam A，無法對齊")
        cam_a = ep.resolve_episode_path(cam_a_rel)
        if not cam_a.is_file():
            raise HTTPException(status_code=404, detail=f"找不到 cam A 檔案：{cam_a_rel}")

        audio_path = (payload.get("audio_path") or "").strip()
        if audio_path:
            audio_file = ep.resolve_episode_path(audio_path)
            if not audio_file.is_file():
                raise HTTPException(status_code=404, detail=f"找不到音檔：{audio_path}")
            try:
                offset_sec = audio_align.auto_align(cam_a, audio_file)
            except RuntimeError as e:
                raise HTTPException(status_code=500, detail=str(e))
            return {"ok": True, "offset_sec": offset_sec}

        cam_b_rel = (payload.get("cam_b_path") or "").strip() or cameras.get("b")
        if not cam_b_rel:
            raise HTTPException(status_code=400, detail="請先在鏡頭 modal 選好 cam B 再對齊")
        cam_b = ep.resolve_episode_path(cam_b_rel)
        if not cam_b.is_file():
            raise HTTPException(status_code=404, detail=f"找不到 cam B 檔案：{cam_b_rel}")
        try:
            offset_sec = audio_align.auto_align(cam_a, cam_b)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"ok": True, "offset_sec": offset_sec}

    @app.post("/api/manual-align")
    def manual_align_route(payload: dict):
        """T23c：使用者手動標三組 (a, b) 時間點 → 算 offset + 一致性 deltas。
        不寫 yaml — 前端拿到 offset 填到 #cam-sync-offset-b，使用者按儲存才走 /api/save。"""
        events = payload.get("events")
        try:
            offset_sec, deltas = audio_align.compute_manual_offset(events)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "offset_sec": offset_sec, "deltas": deltas}

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
