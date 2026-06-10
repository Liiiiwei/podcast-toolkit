# 全 UI 化 podcast-toolkit — Design Spec

- **日期**：2026-06-04
- **狀態**：Draft（待 user review）
- **前置**：[2026-05-27-podcast-edit-ui-design.md](./2026-05-27-podcast-edit-ui-design.md)（edit UI 已上線）

## 目的

讓使用者不再碰 terminal — 雙擊 macOS `.app` 即可進入瀏覽器內的 Dashboard，從那裡選集、新建集、編輯、合成、設定。CLI 保留供 power user / 腳本化使用。

## 範圍

**包含：**
1. macOS `.app` bundle（py2app 打包）作為主要入口
2. Dashboard 頁面：列出已 init 的集（LRU + 自訂根目錄 + 手動選資料夾），點集 card 進入 edit UI
3. 集數階段 badge：⚪ 未轉字幕 / 🟡 未合成 / 🟢 完成
4. 設定中可加 / 移自訂「集數根目錄」
5. `.app` 關閉 = server 關閉；同一時間只允許一個 .app instance（lockfile）
6. CLI 新增 `podcast ui`（不帶 path → Dashboard 模式）；既有 `podcast init / resegment / assemble / edit` 全部保留

**不包含：**
- Electron / Tauri 等桌面殼方案
- 跨平台打包（只做 macOS .app；Linux / Windows 維持 CLI）
- menu bar 常駐 / 開機自啟（.app 關 = server 停）
- 集數模糊搜尋、tag、收藏、回收桶等 Dashboard 進階功能（YAGNI）
- 縮圖 / 影片 cover 等視覺化 metadata

---

## 1. 架構總覽

```
┌────────────────────────────────────────────────────────────────┐
│ Podcast.app (py2app bundle)                                    │
│                                                                │
│  launcher.py（雙擊進入點）                                       │
│    1. 找 free port                                              │
│    2. 取得 ~/.podcast-toolkit/.server.lock（global singleton）  │
│       若已存在且 process 還活 → 開 browser 到既有 port、不重啟    │
│    3. uvicorn.run(build_app(ep=None))                          │
│    4. webbrowser.open("http://127.0.0.1:<port>/")              │
│    5. atexit + SIGTERM handler → 釋放 lockfile + stop uvicorn  │
│                                                                │
│  FastAPI（podcast_toolkit.web.api）                             │
│    Dashboard 模式（holder["ep"] is None）：                      │
│      GET  /                  → dashboard.html                  │
│      GET  /api/episodes      → LRU + 掃自訂 root + stage 判定    │
│      POST /api/episodes/open → 校驗後切到 edit 模式             │
│      POST /api/episode/pick  → osascript（沿用）                │
│      POST /api/episode/new   → wizard（沿用）                   │
│    Edit 模式（holder["ep"] 已選）：                              │
│      GET  /                  → index.html（現有 edit UI）        │
│      所有現有 edit API 沿用，加 _require_ep() guard              │
│    共用：                                                       │
│      GET  /api/config / POST /api/config（加 episode_roots）     │
│      POST /api/episodes/close → 切回 dashboard 模式             │
└────────────────────────────────────────────────────────────────┘
```

**核心改動原則：**
- `build_app(ep)` → `build_app(ep=None)`，holder 是唯一狀態變數
- Dashboard 與 Edit 共用同一 server / 同一 port，靠 `GET /` 依 `holder["ep"]` 路由
- CLI `podcast edit <path>` 維持不變（直接帶 ep 進 Edit 模式，跳過 Dashboard）
- `podcast ui` 與 launcher 走同一路徑：build_app(ep=None) → Dashboard

---

## 2. 元件拆解

### 新增

| 單元 | 責任 | 對外介面 | 依賴 |
|---|---|---|---|
| `podcast_toolkit/launcher.py` | .app 入口：起 uvicorn、開瀏覽器、處理 quit 訊號、global lockfile | `main()`（py2app entry 呼叫） | `uvicorn`、`webbrowser`、`web.api.build_app`、`signal`、`atexit` |
| `podcast_toolkit/web/dashboard.py` | Dashboard 用 logic（純函式，可單測） | `list_episodes(roots, recent) -> {"episodes": list[dict], "warnings": list[str]}`、`add_recent(path)`、`load_recent() / save_recent()`、`episode_stage(ep_dir) -> str` | `episode.Episode`、`pathlib` |
| `podcast_toolkit/web/static/dashboard.html` | Dashboard 頁殼 | — | — |
| `podcast_toolkit/web/static/dashboard.js` | Dashboard 互動 | — | fetch API |
| `podcast_toolkit/web/static/dashboard.css` | Dashboard 樣式 | — | — |
| `setup_app.py` | py2app 設定 | `python setup_app.py py2app` 產出 `dist/Podcast.app` | `py2app` |

### 改動

| 單元 | 改動 |
|---|---|
| `podcast_toolkit/web/api.py` | (a) `build_app(ep=None, shutdown=...)` 允許 None；(b) 新增 `GET /` 依 holder 路由；(c) 新增 `GET /api/episodes`、`POST /api/episodes/open`、`POST /api/episodes/close`；(d) `GET/POST /api/config` 加 `episode_roots: list[str]` 欄位；(e) 所有既有 edit endpoint 加 `_require_ep()` guard，未選集回 409 |
| `podcast_toolkit/cli.py` | 新增 `podcast ui` subcommand（無 path、走 Dashboard 模式） |
| `podcast_toolkit/edit.py` | `run()` 拆 `run_with_ep(ep)` 與 `run_dashboard()`；前者沿用 `podcast edit <path>`、後者給 `podcast ui` 與 launcher 用 |
| `~/.podcast-toolkit/config.json` | schema 加 `episode_roots: list[str]`（預設 `["~/Downloads"]`）、`recent_episodes: list[str]`（最多 20 筆） |
| `04_工作檔/.edit.lock` | 廢除 — 改用 global `~/.podcast-toolkit/.server.lock`，因 Dashboard 模式還沒選集無 04_工作檔 可寫。CLI `podcast edit <path>`、`podcast ui`、launcher 三者共用同一 lockfile，互斥（不允許同時跑兩個 server） |

### Episode 階段判定（`episode_stage()`）

```python
def episode_stage(ep_dir: Path) -> str:
    ep = Episode(ep_dir)  # 失敗 → 上層 catch 回 "broken"
    if not ep.main_video().exists():
        return "empty"           # 不列在 Dashboard
    if not ep.output_v2_srt().exists():
        return "needs_transcribe"
    if not (ep.output_yt_video().exists() or ep.output_reels_video().exists()):
        return "needs_assemble"
    return "done"
```

對應 UI badge：
- ⚪ 未轉字幕（needs_transcribe）
- 🟡 未合成（needs_assemble）
- 🟢 完成（done）
- ⚠ 損毀（broken — episode.yaml 壞掉）

### 邊界檢查
- `dashboard.py` 完全不依賴 FastAPI，純函式 + dataclass，方便寫 unit test
- `api.py` 已接近 600 行，dashboard logic 拆獨立 module 避免再膨脹
- launcher 放 package 內，`setup_app.py` 引用 `from podcast_toolkit.launcher import main`

---

## 3. 資料流

### 序列 A：冷啟動（雙擊 .app → 看到 Dashboard）

```
User              .app/launcher        FastAPI(holder.ep=None)    Browser
 │                  │                       │                       │
 │── 雙擊 .app ────→│                       │                       │
 │                  │── 取 .server.lock ─── │                       │
 │                  │── find_free_port ────│                        │
 │                  │── uvicorn.run(app) ──→│ (listening)           │
 │                  │── webbrowser.open ────────────────────────────→│
 │                  │                       │←── GET / ─────────────│
 │                  │                       │── dashboard.html ─────→│
 │                  │                       │←── GET /api/episodes ─│
 │                  │                       │  ┌─ dashboard.py ─┐    │
 │                  │                       │  │ load_recent()  │    │
 │                  │                       │  │ scan roots     │    │
 │                  │                       │  │ stage 判定      │    │
 │                  │                       │  └────────────────┘    │
 │                  │                       │── {episodes,warnings} →│
```

### 序列 B：從 Dashboard 進入某集

```
Browser                       FastAPI(holder.ep=None)
  │── 點某集 card ────────────────│
  │── POST /api/episodes/open ──→│
  │   {path}                     │── Episode(path) 校驗
  │                              │── holder["ep"] = ep
  │                              │── dashboard.add_recent(path)
  │                              │←── {ok:true}
  │── window.location = "/" ─────│
  │                              │← GET /
  │                              │  ep ≠ None → 回 index.html
```

### 序列 C：在 edit UI 按「← 回 Dashboard」

```
Browser                       FastAPI(holder.ep=ep)
  │── POST /api/episodes/close ─→│
  │                              │── holder["ep"] = None
  │── window.location = "/" ─────│
  │                              │← GET /
  │                              │  ep = None → 回 dashboard.html
```

### 序列 D：關閉 .app

```
User                .app             FastAPI                Browser
 │── Cmd+Q .app ────→│                  │                     │
 │                   │── atexit hook ──→│ should_exit = True  │
 │                   │                  │── socket close ─────→│
 │                   │                  │── lockfile 清除      │
```

**反向**：使用者在瀏覽器按「結束」→ `POST /api/shutdown`（既有）→ uvicorn exit → launcher 偵測 exit → 退 .app

### 狀態流轉
```
holder["ep"]:  None ──[/api/episodes/open]──→ Episode
                 ↑                              │
                 └──[/api/episodes/close]───────┘
```
單一狀態變數，server 進程內共享。.app : server : holder = 1 : 1 : 1。

### `recent_episodes` 寫入時機
- `POST /api/episodes/open` 成功時 prepend、去重、最多 20 筆
- 寫入 `~/.podcast-toolkit/config.json`，atomic：寫 `config.json.tmp` 再 rename

---

## 4. 錯誤處理

### 啟動失敗
| 情境 | 處理 |
|---|---|
| port 找不到（OS 整池耗盡） | launcher 印 stderr + `osascript -e 'display alert ...'` 跳原生對話框、exit 1 |
| uvicorn 起不來 | 同上，traceback dump 到 `~/.podcast-toolkit/launcher.log` |
| 已有另一個 .app instance 在跑 | 不重啟，直接 `webbrowser.open` 既有 instance URL（讀 lockfile 拿 port） |

### Dashboard 模式錯誤
| 情境 | 處理 |
|---|---|
| `~/Downloads/` 不存在 | `list_episodes` 跳過該 root、回 `warnings: ["~/Downloads not found"]`、UI 顯示黃色提示 |
| 自訂 root 路徑壞掉 | 同上 |
| `episode.yaml` 損毀 | 該集顯示 ⚠ 損毀 card，點擊 alert 訊息，不擋整個列表 |
| `recent_episodes` JSON 損毀 | 視為空、繼續啟動、下次寫入時覆蓋 |
| 點集 card 時資料夾已被刪 | `POST /api/episodes/open` 回 400、UI toast + refresh 列表 |

### Edit 模式錯誤
- 既有 endpoint 加 `_require_ep()` guard：`holder["ep"] is None` 時回 409「請先在 Dashboard 選集」（防 deep-link 直接戳 edit URL）

### 關閉與清理
- launcher 註冊 `atexit` + `signal.SIGTERM` handler：清 lockfile、stop uvicorn
- macOS Cmd+Q：py2app 預設會發 `NSApplicationWillTerminate`，靠 atexit 接住（PyObjC hook 過於邊緣，靠 lockfile 殘留檢測 + 自動清理當保險：launcher 啟動時若發現 lockfile pid 已死則自動清掉）

### 取消行為
- 使用者點瀏覽器「取消」→ `POST /api/shutdown` → server exit（沿用）
- 使用者關掉 browser tab → server **不**退（.app 還活著），重開瀏覽器到同 port 即可繼續

---

## 5. 測試

### Unit test
`tests/test_dashboard.py`
- `episode_stage()` 五分支：empty / needs_transcribe / needs_assemble / done / broken yaml
- `list_episodes()`：roots 不存在跳過、去重（LRU 與 root 掃描同一集只列一次）、依 mtime 排序
- `load_recent()` / `save_recent()`：壞 JSON 視為空、最多 20 筆、prepend + 去重
- atomic write：模擬寫入中斷後 config 不會半壞（寫 .tmp 再 rename）

### Integration test
`tests/test_api_dashboard.py`（FastAPI `TestClient`，不起真 server）
- `GET /` 在 ep=None 時回 dashboard.html
- `GET /` 在 ep=Episode 時回 index.html
- `POST /api/episodes/open` 切換 holder + 寫 recent
- `POST /api/episodes/close` 清空 holder
- 既有 endpoint（如 `/api/save`）在 ep=None 時回 409
- 開不存在 path → 400
- 開無 episode.yaml 的資料夾 → 400

### 手動驗收（無法自動化）
- `python setup_app.py py2app` 產出 .app、雙擊真的開瀏覽器進 Dashboard
- Cmd+Q .app 後 lockfile 清掉、port 真的釋放
- 第二次雙擊已在跑的 .app → 開瀏覽器到既有 instance，不起新 server
- 在 Dashboard 設定自訂 root（如 `~/Podcasts/`）→ 重啟 .app 後讀取到
- Dashboard → 進某集 → 編輯 → 回 Dashboard → 再進另一集，holder 狀態正確切換

### 不測
- py2app 打包過程（依賴 py2app 自身；CI 不跑，只在 release 跑）
- macOS 原生 quit 訊號（PyObjC hook 邊緣，靠 lockfile 自動清理當保險）
- 瀏覽器 UI 互動（dashboard.js 邏輯簡單，靠手動驗收）

---

## 開放問題

無 — 設計階段所有選項都已選定。實作時若發現需要決策的細節（例：dashboard 是否做 sort by date 或 by stage），會回頭問。

## 相關文件
- 既有 edit UI 規格：[2026-05-27-podcast-edit-ui-design.md](./2026-05-27-podcast-edit-ui-design.md)
- 現有 API 入口：`podcast_toolkit/web/api.py`
- CLI 入口：`podcast_toolkit/cli.py`
