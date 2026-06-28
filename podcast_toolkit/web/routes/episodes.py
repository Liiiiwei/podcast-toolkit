"""集的生命週期：dashboard 列表、開/關/切換/新建/init/預覽/挑資料夾。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from podcast_toolkit import init as ep_init
from podcast_toolkit.episode import Episode
from podcast_toolkit.web import dashboard as dashboard_mod
from podcast_toolkit.web import episode_io
from podcast_toolkit.web.shared import SKIP_DIRS, STATIC_DIR, RouteContext


def _tcc_error_html(blocked_dir: str) -> str:
    """macOS TCC 擋住靜態檔時回的明確錯誤頁。

    必須完全 inline、不引用任何 /static 資源——因為那些檔正是被擋的對象，
    用 FileResponse 或外連 css/js 一樣會空白。樣式內嵌、走 Studio Dark 色。
    """
    safe_dir = (
        blocked_dir.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>無法載入編輯器 — 權限問題</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; background: #0e0e11; color: #ebebee;
    font-family: "IBM Plex Sans", -apple-system, system-ui, sans-serif;
    line-height: 1.6; padding: 24px;
  }}
  .card {{
    max-width: 640px; background: #18181d; border: 1px solid #2a2a31;
    border-left: 3px solid #ff6b6b; border-radius: 10px; padding: 28px 32px;
  }}
  h1 {{ font-size: 18px; margin: 0 0 4px; color: #ff6b6b; }}
  .sub {{ font-size: 13px; color: #9a9aa3; margin: 0 0 20px; }}
  p {{ font-size: 14px; margin: 12px 0; }}
  ol {{ font-size: 14px; padding-left: 22px; margin: 12px 0; }}
  li {{ margin: 8px 0; }}
  code, .path {{
    font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 13px;
    background: #0e0e11; border: 1px solid #2a2a31; border-radius: 5px;
    padding: 2px 6px; color: #f0b95a; word-break: break-all;
  }}
  .path {{ display: block; padding: 10px 12px; margin: 6px 0 0; }}
  .hint {{ font-size: 12px; color: #9a9aa3; margin-top: 20px; }}
</style>
</head>
<body>
  <div class="card">
    <h1>無法載入編輯器</h1>
    <p class="sub">macOS 權限（TCC）擋住了 toolkit 讀取自己的程式檔</p>
    <p>編輯器的靜態檔放在被系統保護的資料夾，server 能看到檔案卻無法開啟讀取，
       因此頁面會整片空白。被擋的目錄：</p>
    <span class="path">{safe_dir}</span>
    <p>請擇一處理後重啟 server：</p>
    <ol>
      <li>把 toolkit 安裝目錄移出 <code>~/Desktop</code>、<code>~/Downloads</code>、
          <code>~/Documents</code>（例如改放 <code>~/podcast-toolkit</code>）——最乾淨。</li>
      <li>或到「系統設定 › 隱私權與安全性 › 完全取用磁碟」，把實際執行的
          Python／終端機 App 加進去並開啟。</li>
    </ol>
    <p class="hint">處理完請重新啟動 podcast-toolkit server，本頁會自動恢復成正常編輯器。</p>
  </div>
</body>
</html>"""


def register(app: FastAPI, ctx: RouteContext) -> None:
    holder = ctx.holder

    @app.get("/")
    def index():
        # 同一個 URL 會依 holder 狀態回 dashboard.html 或 index.html，
        # 沒設 no-store 的話 Chromium 會用啟發式快取直接吃 cache 不打 server，
        # 導致 open 完 redirect 回 / 還是看到舊頁
        headers = {"Cache-Control": "no-store"}
        # 靜態檔被 TCC 擋時，回 index.html/dashboard.html 一樣讀不到 body → 整頁空白。
        # 改回明確 inline 錯誤頁（不碰任何 /static），告訴使用者真正原因與解法。
        blocked = getattr(app.state, "tcc_blocked_dir", None)
        if blocked:
            return HTMLResponse(
                _tcc_error_html(blocked), status_code=503, headers=headers
            )
        if holder["ep"] is None:
            return FileResponse(STATIC_DIR / "dashboard.html", headers=headers)
        return FileResponse(STATIC_DIR / "index.html", headers=headers)

    @app.get("/api/episodes")
    def list_episodes():
        cfg = ctx.load_config()
        roots = cfg.get("episode_roots") or [str(Path.home() / "Downloads")]
        recent = dashboard_mod.load_recent(ctx.get_config_path())
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
        dashboard_mod.add_recent(ctx.get_config_path(), str(target))
        return JSONResponse({"ok": True})

    @app.post("/api/episodes/close")
    def close_episode():
        holder["ep"] = None
        return JSONResponse({"ok": True})

    @app.get("/api/episode")
    def get_episode():
        # no-store：否則 Chromium 啟發式快取會吃舊 cache 不打 server，存檔後 loadEpisodeState
        # 重抓 /api/episode 拿到的是存檔前資料 → 拖拉/新增/微調等編輯「存了卻沒反映在 UI」。
        return JSONResponse(
            episode_io.load_state(ctx.require_ep()),
            headers={"Cache-Control": "no-store"},
        )

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
            cfg = ctx.load_config()
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
        dashboard_mod.add_recent(ctx.get_config_path(), str(target))
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
