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
from podcast_toolkit import config as pt_config
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
COMMON_GLOSSARY_PATH = CONFIG_DIR / "common-glossary.json"
EPISODE_GLOSSARY_FILENAME = ".glossary.json"

# 可轉字幕的副檔名（含音訊與含音訊軌的影片）
TRANSCRIBABLE_EXTS = {
    ".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus",
    ".mp4", ".mov", ".mkv", ".webm",
}
# 可在瀏覽器直接預覽的影片副檔名
PREVIEWABLE_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
# 可在瀏覽器直接預覽的音檔副檔名 + MIME 對照
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".opus"}
AUDIO_MIME = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
}
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


def _normalize_glossary_entries(raw: list) -> list[dict]:
    """整理使用者貼進來的詞庫條目；canonical 為主鍵，去重保留最後一筆。
    支援純字串簡寫（同 config.normalize_glossary）→ {canonical, sounds_like, note}。
    """
    seen: dict[str, dict] = {}
    for item in raw or []:
        if isinstance(item, str):
            c = item.strip()
            if c:
                seen[c] = {"canonical": c, "sounds_like": [], "note": ""}
            continue
        if not isinstance(item, dict):
            continue
        canonical = (item.get("canonical") or "").strip()
        if not canonical:
            continue
        sounds_like_raw = item.get("sounds_like") or []
        sounds_like = []
        if isinstance(sounds_like_raw, list):
            sounds_like = [str(s).strip() for s in sounds_like_raw if str(s).strip()]
        seen[canonical] = {
            "canonical": canonical,
            "sounds_like": sounds_like,
            "note": str(item.get("note", "")),
        }
    return list(seen.values())


def _load_glossary_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return _normalize_glossary_entries(data)


def _save_glossary_file(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_common_glossary() -> list[dict]:
    return _load_glossary_file(COMMON_GLOSSARY_PATH)


def _save_common_glossary(entries: list[dict]) -> None:
    _save_glossary_file(COMMON_GLOSSARY_PATH, entries)


def _episode_glossary_path(ep_dir: Path) -> Path:
    return ep_dir / EPISODE_GLOSSARY_FILENAME


def _load_episode_glossary(ep_dir: Path) -> list[dict]:
    return _load_glossary_file(_episode_glossary_path(ep_dir))


def _save_episode_glossary(ep_dir: Path, entries: list[dict]) -> None:
    _save_glossary_file(_episode_glossary_path(ep_dir), entries)


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


def _check_assets_status() -> dict:
    """檢查 defaults.yaml 指向的共用資產檔案是否存在。
    給前端 settings 顯示 ✓/✗ 用，避免第一次合成才發現缺檔。
    """
    try:
        defaults = pt_config.load_defaults()
    except Exception:
        return {}
    root = pt_config.toolkit_root()
    out: dict[str, dict] = {}
    for key in ("intro", "outro_audio", "outro_image", "logo"):
        rel = (defaults.get("assets") or {}).get(key)
        if not rel:
            continue
        p = (root / rel).resolve()
        out[key] = {"path": rel, "exists": p.is_file()}
    return out


def _list_episode_files(root: Path) -> list[dict]:
    """遞迴列出集資料夾內所有檔案，標註 kind / 字幕角色。"""
    files: list[dict] = []
    try:
        ep = Episode(root)
        main_video_path = ep.main_video()
        main_srt_path = ep.main_srt()
        # active_srt 反映 cam-modal 手選；override 沒設時仍會等於 _v2.srt
        active_srt_path = ep.active_srt()
        yt_out = ep.output_yt_video()
        reels_out = ep.output_reels_video()
    except Exception:
        main_video_path = main_srt_path = active_srt_path = None
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
        elif active_srt_path and p == active_srt_path:
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
        # `activate` 把 osascript 自己拉到最前面，否則 background 起的 server
        # 跳 choose folder 對話框會被 Chrome / VSCode 蓋住，使用者誤以為失敗
        script = (
            'tell me to activate\n'
            f'POSIX path of (choose folder with prompt "選擇集資料夾" '
            f'default location POSIX file "{default_dir}")'
        )
        print(f"[pick] default_dir={default_dir!r}", flush=True)
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except FileNotFoundError:
            print("[pick] osascript not found", flush=True)
            raise HTTPException(status_code=500, detail="找不到 osascript（需 macOS）")
        except subprocess.TimeoutExpired:
            print("[pick] osascript timeout (300s)", flush=True)
            return JSONResponse({"path": None, "cancelled": True})
        print(
            f"[pick] rc={result.returncode} stdout={result.stdout!r} "
            f"stderr={result.stderr!r}",
            flush=True,
        )
        if result.returncode != 0:
            # 使用者取消（rc=1，stderr 含 "User canceled"）或其他錯誤
            return JSONResponse({"path": None, "cancelled": True})
        picked = result.stdout.strip().rstrip("/")
        if not picked:
            return JSONResponse({"path": None, "cancelled": True})
        return JSONResponse({"path": picked, "cancelled": False})

    @app.post("/api/episode/preview")
    def preview_episode(payload: dict):
        """預覽資料夾內容（給沒 episode.yaml 的資料夾用）。"""
        raw = (payload.get("path") or "").strip()
        print(f"[preview] raw={raw!r}", flush=True)
        if not raw:
            raise HTTPException(status_code=400, detail="缺少 path")
        target = Path(os.path.expanduser(raw)).resolve()
        if not target.is_dir():
            print(f"[preview] not a dir: {target}", flush=True)
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
        has_yaml = (target / "episode.yaml").is_file()
        print(
            f"[preview] target={target} has_yaml={has_yaml} "
            f"date={date!r} name={name!r} entries={len(entries)}",
            flush=True,
        )
        return JSONResponse({
            "path": str(target),
            "folder_name": target.name,
            "has_episode_yaml": has_yaml,
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
        if ep is not None:
            parent = ep.dir.parent
        else:
            # 沒有 active ep（從 dashboard 開新集）→ 用 config 第一個 root，
            # 否則 default 到 ~/Downloads。這樣新集才會被 dashboard 掃到。
            cfg = _load_config()
            roots = cfg.get("episode_roots") or []
            parent = Path(os.path.expanduser(roots[0])) if roots else (Path.home() / "Downloads")
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
        # 與 open_episode 一致：新建集也要進 recent，這樣即使集落在 roots 之外
        # dashboard 仍能看到。
        dashboard_mod.add_recent(CONFIG_PATH, str(target))
        return JSONResponse({
            "ok": True,
            "path": str(target),
            "name": new_ep.name,
        })

    @app.post("/api/episode/switch")
    def switch_episode(payload: dict):
        raw = (payload.get("path") or "").strip()
        print(f"[switch] raw={raw!r}", flush=True)
        if not raw:
            raise HTTPException(status_code=400, detail="缺少 path")
        new_dir = Path(os.path.expanduser(raw)).resolve()
        if not new_dir.is_dir():
            print(f"[switch] not a dir: {new_dir}", flush=True)
            raise HTTPException(status_code=400, detail=f"資料夾不存在：{new_dir}")
        if not (new_dir / "episode.yaml").is_file():
            print(f"[switch] no episode.yaml in: {new_dir}", flush=True)
            raise HTTPException(
                status_code=400,
                detail=f"不是 episode 資料夾（缺 episode.yaml）：{new_dir}",
            )
        try:
            new_ep = Episode(new_dir)
        except Exception as e:
            print(f"[switch] Episode() failed: {e!r}", flush=True)
            raise HTTPException(status_code=400, detail=f"無法載入 episode：{e}")
        holder["ep"] = new_ep
        print(f"[switch] ok → name={new_ep.name!r} dir={new_ep.dir}", flush=True)
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
            # 空集（init 完但 01_母帶/ 沒檔）main_video 解析後不存在 → 回 404
            # 不要讓 range_response 的 path.stat() 直接拋 FileNotFoundError 變成 500 噪音
            if not target.is_file():
                raise HTTPException(status_code=404, detail="這集還沒有主影片")
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

    @app.get("/api/audio")
    def get_audio(request: Request, path: str):
        ep = _require_ep()
        if not path:
            raise HTTPException(status_code=400, detail="缺少 path")
        target = (ep.dir / path).resolve()
        try:
            target.relative_to(ep.dir)
        except ValueError:
            raise HTTPException(status_code=400, detail="路徑必須在集資料夾內")
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"找不到檔案：{path}")
        ext = target.suffix.lower()
        if ext not in AUDIO_EXTS:
            raise HTTPException(status_code=400, detail="不支援預覽的音檔副檔名")
        mime = AUDIO_MIME.get(ext, "audio/mpeg")
        return video.range_response(target, request.headers.get("range"), media_type=mime)

    @app.post("/api/save")
    def save(payload: dict):
        ep = _require_ep()
        episode_io.save_state(ep, payload)
        # 重新 init Episode 讓 cfg 反映剛寫入的 yaml；否則 GET /api/episode
        # 還是拿 build_app 當下 cache 的 cfg，A/B toggle 等依賴 refetch 的 UI 不會更新
        holder["ep"] = Episode(ep.dir)
        return {"ok": True}

    @app.post("/api/episode/mics")
    def post_episode_mics(payload: dict):
        """寫 mics 設定到 episode.yaml。前端在開分軌轉錄前發現 yaml 沒設 mics 時呼叫。

        payload: {"mics": {"a": "01_母帶/Track1.wav", "b": "...", "c": "..."}}
        - speaker key 必須是 a/b/c
        - path 是相對 episode 根的相對路徑（用既有 audio_candidates 同款格式）
        - 檔案必須存在，且要落在 episode 資料夾內（防 ../ 逸出）
        """
        ep = _require_ep()
        mics = payload.get("mics") or {}
        if not isinstance(mics, dict) or not mics:
            raise HTTPException(status_code=400, detail="mics 必須是 {speaker: path} 物件")
        allowed = {"a", "b", "c"}
        for sp, path in mics.items():
            if sp not in allowed:
                raise HTTPException(status_code=400, detail=f"speaker {sp!r} 不在允許範圍 {sorted(allowed)}")
            if not isinstance(path, str) or not path.strip():
                raise HTTPException(status_code=400, detail=f"{sp} 的路徑不能空")
            target = (ep.dir / path).resolve()
            try:
                target.relative_to(ep.dir.resolve())
            except ValueError:
                raise HTTPException(status_code=400, detail=f"{sp} 路徑必須在集資料夾內")
            if not target.is_file():
                raise HTTPException(status_code=404, detail=f"{sp} 找不到檔案：{path}")
        episode_io.save_mics_config(ep, mics)
        holder["ep"] = Episode(ep.dir)
        return {"ok": True, "mics": dict(sorted(mics.items()))}

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
        # xai 已從設定面板下架；舊 config 殘留 "xai" 一律回退 gemini
        provider = (cfg.get("transcribe") or {}).get("provider") or "gemini"
        if provider not in transcribe.PROVIDERS or provider == "xai":
            provider = "gemini"
        return JSONResponse({
            "has_xai_api_key": bool(cfg.get("xai_api_key")),
            "has_gemini_api_key": bool(cfg.get("gemini_api_key")),
            "has_openai_api_key": bool(cfg.get("openai_api_key")),
            "provider": provider,
            "episode_roots": cfg.get("episode_roots") or [str(Path.home() / "Downloads")],
            "assets": _check_assets_status(),
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
        if "openai_api_key" in payload:
            key = (payload.get("openai_api_key") or "").strip()
            if key:
                cfg["openai_api_key"] = key
            else:
                cfg.pop("openai_api_key", None)
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
        out_provider = (cfg.get("transcribe") or {}).get("provider") or "gemini"
        if out_provider == "xai":
            out_provider = "gemini"
        return JSONResponse({
            "has_xai_api_key": bool(cfg.get("xai_api_key")),
            "has_gemini_api_key": bool(cfg.get("gemini_api_key")),
            "has_openai_api_key": bool(cfg.get("openai_api_key")),
            "provider": out_provider,
            "episode_roots": cfg.get("episode_roots") or [str(Path.home() / "Downloads")],
            "assets": _check_assets_status(),
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
        provider = (cfg.get("transcribe") or {}).get("provider") or "gemini"
        if provider == "xai":
            provider = "gemini"
        if provider not in transcribe.PROVIDERS:
            raise HTTPException(
                status_code=400, detail=f"未知的 STT 供應商：{provider}"
            )
        key_map = {
            "xai": "xai_api_key",
            "gemini": "gemini_api_key",
            "openai": "openai_api_key",
        }
        label_map = {
            "xai": "xAI",
            "gemini": "Gemini",
            "openai": "OpenAI",
            "whisper_mlx": "本地 Whisper",
        }
        # 本地 provider 不需 key；雲端 provider 缺 key 直接擋
        api_key = cfg.get(key_map.get(provider, ""), "") or ""
        if provider in key_map and not api_key:
            raise HTTPException(
                status_code=400,
                detail=f"尚未設定 {label_map[provider]} API key",
            )

        # yaml glossary + UI 編輯（全域 + 本集）→ canonical 為主鍵去重
        merged_glossary = _normalize_glossary_entries(
            (ep.cfg.get("glossary") or [])
            + _load_common_glossary()
            + _load_episode_glossary(ep.dir)
        )
        try:
            info = transcribe_job.start_job(
                ep,
                src_rel=rel,
                provider=provider,
                api_key=api_key,
                typo_entries=_load_typo_dict(),
                glossary=merged_glossary,
            )
        except RuntimeError as e:
            # 已有 job 在跑
            raise HTTPException(status_code=409, detail=str(e))

        return JSONResponse(
            {"ok": True, "src_path": info["src_path"]},
            status_code=202,
        )

    @app.post("/api/transcribe/per-mic")
    def post_transcribe_per_mic(payload: dict):
        """分軌轉錄：背景跑 N 路 Gemini 同步 → srt_merge → _final_v2.srt + speakers.json。

        payload: {"speakers": ["a", "b", "c"]} — 必填，要跑的軌子集。
        """
        ep = _require_ep()
        speakers = payload.get("speakers") or []
        if not isinstance(speakers, list) or not all(isinstance(s, str) for s in speakers):
            raise HTTPException(status_code=400, detail="speakers 必須是字串陣列")
        if not speakers:
            raise HTTPException(status_code=400, detail="speakers 不能是空清單")

        cfg = _load_config()
        api_key = cfg.get("gemini_api_key")
        if not api_key:
            raise HTTPException(status_code=400, detail="尚未設定 Gemini API key")
        # transcribe_per_mic 直接讀 env 變數
        os.environ["GEMINI_API_KEY"] = api_key

        try:
            info = transcribe_job.start_per_mic_job(ep, speakers=speakers, force=True)
        except RuntimeError as e:
            # 已有 job 在跑 / 不認得的 speaker / mics 沒設
            raise HTTPException(status_code=409, detail=str(e))

        return JSONResponse(
            {"ok": True, "speakers": info["speakers"]},
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
        preview_sec_raw = payload.get("preview_sec")
        preview_sec: int | None = None
        if preview_sec_raw is not None:
            try:
                preview_sec = int(preview_sec_raw)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="preview_sec 必須是正整數")
            if preview_sec <= 0:
                preview_sec = None
        try:
            info = assemble_job.start_job(
                ep, targets=targets, force=force, preview_sec=preview_sec,
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

        ep = _require_ep()
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

        cam A 也走同樣的「payload 優先」邏輯（payload['cam_a_path']），
        讓使用者在 modal 改 cam A 後不必先按儲存就能對齊。
        """
        ep = _require_ep()
        payload = payload or {}
        cameras = ep.cfg.get("cameras") or {}
        cam_a_rel = (payload.get("cam_a_path") or "").strip() \
            or cameras.get("a") or ep.cfg.get("main_video")
        if not cam_a_rel:
            raise HTTPException(status_code=400, detail="缺 cam A，無法對齊")
        cam_a = ep.resolve_episode_path(cam_a_rel)
        if not cam_a.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"找不到 cam A 檔案：{cam_a_rel}（解析後：{cam_a}）",
            )

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

    @app.get("/api/glossary")
    def get_glossary():
        """回傳 {episode: [...], common: [...], yaml: [...]}。
        yaml 段是 episode.yaml + defaults.yaml 內既有的（唯讀，提示用），
        episode / common 是 UI 可編輯的 JSON sidecar。
        """
        ep = _require_ep()
        return JSONResponse({
            "episode": _load_episode_glossary(ep.dir),
            "common": _load_common_glossary(),
            "yaml": ep.cfg.get("glossary") or [],
        })

    @app.post("/api/glossary/common")
    def post_glossary_common(payload: dict):
        entries = _normalize_glossary_entries(payload.get("entries") or [])
        _save_common_glossary(entries)
        return JSONResponse(entries)

    @app.post("/api/glossary/episode")
    def post_glossary_episode(payload: dict):
        ep = _require_ep()
        entries = _normalize_glossary_entries(payload.get("entries") or [])
        _save_episode_glossary(ep.dir, entries)
        return JSONResponse(entries)

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
