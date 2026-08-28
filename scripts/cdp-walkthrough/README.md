# CDP 走查腳本（影片模式原型）

這裡是 2026-08 開發「影片模式時間軸／字卡」原型時，用 Chrome DevTools Protocol
驅動 headless Chrome 跑的**走查腳本原檔**。原本散在 `/private/tmp/`（重開機就沒了），
這次原封不動收進版控保存。

> **它們不是可以直接跑的測試套件。**
> 每支都寫死了當時的沙盒路徑、連接埠、甚至 Chrome 的 WebSocket UUID。
> 價值在於「當時驗過什麼、怎麼驗的」的紀錄，以及可以抄的斷言片段。
> 要真的跑，得先照下面「怎麼讓它跑起來」把環境重建出來。

## 目錄

| 目錄 | 來源 | 內容 |
|------|------|------|
| `vt-cdp/` | `/private/tmp/vt-cdp` | 52 支。主力走查：`drive.py`、`drive2.py`–`drive39.py`（無 drive18）、診斷用 `diag*.py`／`probe*.py`、截圖用 `shot*.py`、突變測試 `mut34.py` |
| `vt-proto/` | `/private/tmp/vt-proto` | 6 支。早期原型走查 `drive.py`／`drive2.py`／`drive_subdrag.py`、點擊命中診斷 `probe_click*.py`，以及 **`range_server.py`** |
| `vt-realmode/` | 2026-08-27 session scratchpad | 3 支。真模式走查 `drive_entry.py`（UI 入口／真模式載真字幕／確認框不分模式都跳，含逐項突變測試）、`drive_b3.py`（改真集的 `_final_v2.srt` 證明頁面讀的是那個檔，`finally` 還原），以及它們的伴生伺服器 **`serve_sandbox.py`** |

`vt-cdp/` 與 `vt-proto/` 各有一支同名 `drive.py`，內容不同，所以分開放。

`vt-realmode/` 的兩支走查**必須先跑 `serve_sandbox.py`**（把 `build_app` 綁到沙盒集起在 8792，
刻意不寫 lockfile 以免干擾使用者正在跑的 instance）。它們打的是 8792，沒有這支就整批連不上。

`vt-proto/range_server.py` 是這裡面唯一「拿來就能用」的東西：一支支援 HTTP Range 的靜態伺服器。
`python -m http.server` 不回 206，瀏覽器會判定影片不可 seek（`seekable.length === 0`）而且**不拋任何錯**，
所有跟時間軸有關的斷言都會靜默失效。要跑任何含影片的走查，一律用它。

## 受測對象

`podcast_toolkit/web/static/video-edit-prototype.html?demo=1`，
搭配同目錄的 `sample-video.mp4`（640×360／24 秒／合成彩條測試訊號）、
`sample-waveform.json`、`sample-subtitles.json`。demo 模式不需要開集或後端。

`vt-realmode/` 例外：它驗的正是「真模式」，所以要有後端（`serve_sandbox.py`）＋一個真的集
（沙盒集 `/private/tmp/pt-e2e/20260825 端對端測試`），頁面走的是 `/static/…`（不帶 `?demo=1`）。

## 怎麼讓它跑起來

1. **Python 要 3.12 以上。** 這些腳本當時跑在 `/private/tmp/pt-venv`（Python 3.14）。
   其中 `drive19.py`／`drive25.py`／`drive26.py` 用了 f-string 內含反斜線的寫法，
   **在 Python 3.9／3.11 會直接 SyntaxError**（本機 `/usr/bin/python3` 是 3.9.6，跑不動）。
   需要 `websockets` 套件。
   （`vt-realmode/` 的三支沒用到那種寫法，在 `/usr/bin/python3` 3.9.6 跑得動 —— 而且**必須**用它，
   因為後端要 `fastapi`／`uvicorn`／`websockets`，Homebrew 的 python3 缺 `audioop`。）
2. **起 Range 伺服器**：`cd` 到 `podcast_toolkit/web/static/`，`PORT=8791 python3 <本目錄>/vt-proto/range_server.py`。
3. **起 headless Chrome**：`--remote-debugging-port=9333 --user-data-dir=<全新目錄>`。
   用 Bash 工具的 `run_in_background` 起，不要用 `nohup ... &`（工具呼叫結束會把子程序殺掉）。
4. **改腳本裡寫死的路徑**（見下）。
5. 跑的時候加 `python3 -u`，否則 stdout 全緩衝，卡住時看不出卡在第幾步。

## 寫死的路徑（要跑就得先改）

| 出現位置 | 寫死的值 | 說明 |
|----------|----------|------|
| `vt-cdp/` 多處（52 處） | `/private/tmp/vt-cdp` | 截圖／輸出的落點 |
| `vt-cdp/` 部分 | `/private/tmp/pt-e2e/20260825 端對端測試` | 端對端走查用的假集 |
| `vt-cdp/drive39.py:3` | `exec(open("/private/tmp/vt-cdp/drive11.py")…)` | **跨檔相依**：從 drive11 借共用的 CDP 類別 |
| `vt-cdp/mut34.py:2`、`vt-proto/drive*.py` | `/Users/Mac365/conductor/workspaces/podcast-toolkit/colombo/…` | 當時 Conductor worktree 的絕對路徑 |
| `vt-proto/drive.py:7` | `ws://127.0.0.1:9223/devtools/browser/<UUID>` | 一次性的 Chrome UUID，早就失效 |
| `vt-realmode/serve_sandbox.py:4` | `/Users/Mac365/conductor/workspaces/podcast-toolkit/colombo` | `sys.path` 插入點，換 worktree 要改 |
| `vt-realmode/serve_sandbox.py:11`、`drive_b3.py:84` | `/private/tmp/pt-e2e/20260825 端對端測試` | 沙盒集；`drive_b3.py` 會就地改它的 `_final_v2.srt` 再還原 |
| 各處 | port 8791／8792／8877（站台）、9333／9223（CDP） | 連接埠 |

刻意保留原樣沒有改寫 —— 現在改了也沒辦法重跑驗證，留原檔至少是誠實的紀錄。

## 環境踩過的坑（每次都會再踩一次）

- **同一份程式碼兩次跑出不同結果** → 先查環境，不要改產品碼。開場先斷言
  `video.readyState >= 1 && video.seekable.length > 0`；`seekable.length === 0` 是
  「伺服器不支援 Range」唯一的訊號。
- **整批走查突然開始失敗、影片永遠載不完**（`readyState=0`、`error=null`）→ 多半是
  Chrome 的 `user-data-dir` 媒體快取壞了。換一個全新 profile 目錄重啟就好。
  排查順序：伺服器（`curl -r 0-1000` 看有沒有 206）→ 瀏覽器程序（`/json/version` 有沒有回應）→ profile。
- **測試站台是副本時**，改完原始碼忘了同步，症狀長得跟「功能沒生效」一模一樣。
  走查開場先斷言一個只有新版才有的字串當版本指紋，不符就中止。
- **`timeout` 指令這台機器沒有**（沒裝 GNU coreutils），要限時用 `curl -m`。

## 斷言紀律（抄片段時一起抄走）

- 每個 `✓` 都必須綁在布林斷言上（`ok = 實得 == 期待`）。只 `print` 值不斷言的行一律視為未測 ——
  `vt-cdp/drive20.py` 的前身就長期印著三個錯值配一個 ✓。
- 每寫一支走查就配一次突變測試（`mut34.py` 是範例）：把受測的那行改回舊行為，確認該支會紅。
  功能有兩個以上獨立部件時要**逐一關**，只有「全開 vs 全關」抓不到死碼。
- 有絕對定位疊層的區域，要用 `document.elementFromPoint(cx, cy)` 斷言最上層真的是那顆按鈕；
  CDP 打的是座標，跟真人一樣會被疊層吃掉。
- 頁面內的測試 hook 第一次用之前先 `typeof` 確認是物件還是函式 —— 猜錯的下場是
  `evaluate` 回 `None` 而不是報錯，整段檢查靜默不執行。
