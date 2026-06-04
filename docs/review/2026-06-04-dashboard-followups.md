# Dashboard 後續修補清單

**Source:** `/review` 跑於 `vs/edit-ui` branch（2026-06-04）後挑出的 Path A 已修，這份是 **Path A 範圍外**、需另開 PR 處理的項目。
**Path A 已完成（commit `b73b77b`）：** P1#1 async 按鈕 disabled、P1#2 modal Enter 鍵不誤關、P1#3 error 狀態獨立紅底。

---

## 1. Dashboard ↔ Edit UI 缺少回頭路（UX 缺口）

**現象**
從 dashboard 點 episode card → 成功 redirect 到 edit UI，但 edit UI 上沒有「回 dashboard」按鈕。使用者只能殺 server 重啟才能回列表。

**根因**
`podcast_toolkit/web/api.py` 的 `/` route：
- `holder["ep"] is None` → 服務 `dashboard.html`
- `holder["ep"]` 已設 → 服務 `index.html`（edit UI）

切到 edit UI 後沒有 API 可以把 `holder["ep"]` 設回 `None`，前端也沒對應按鈕。

**建議修法**
1. 新增 `POST /api/episode/close`：將 `holder["ep"] = None`
2. Edit UI topbar 加「← 回列表」按鈕呼叫該 API，成功後 `window.location.href = "/"`

**影響檔案**
- `podcast_toolkit/web/api.py`（新 endpoint）
- `podcast_toolkit/web/static/index.html`（topbar 加按鈕）
- `podcast_toolkit/web/static/app.js`（click handler）

---

## 2. Edit UI 在某些 episode 狀態下回 409

**現象**
從 dashboard 點 card 開啟某些 episode 後，edit UI 載入 `app.js`：
```
GET /api/video 409 (Conflict)
GET /api/episode 409 (Conflict)
GET /api/files 409 (Conflict)
Error: /api/episode HTTP 409  at loadEpisodeState (app.js:510:20)
```

**根因（待驗證）**
推測是 edit UI 預期某些檔案（main_video、episode.yaml 某些欄位、files/ 目錄結構）存在，但被打開的 episode 沒有 → 後端 raise 409。

**待做**
1. 在 `api.py` 找出回 409 的 endpoint（grep `409` 或 `HTTPException`）
2. 釐清：是 edit UI 本來就只能開「已完整」episode（那 dashboard 不該讓使用者點未完成集數），還是 edit UI 應該 graceful degrade
3. 二選一：
   - **Dashboard 端守門**：未滿足條件的 episode card 顯示 disabled，附 tooltip 說明缺什麼
   - **Edit UI 端容錯**：缺檔時顯示 placeholder + 引導使用者補齊

**影響檔案**
- `podcast_toolkit/web/api.py`
- `podcast_toolkit/web/static/app.js`
- 可能 `podcast_toolkit/web/static/dashboard.js`（card disabled 邏輯）

---

## 3. 新建集數立刻被 dashboard 過濾掉

**現象**
按「📅 新建集」建立 `20260604 test` 後，回到 dashboard 列表看不到它。

**根因**
`podcast_toolkit/web/dashboard.py:78-79`：
```python
def _episode_meta(ep_dir: Path) -> dict | None:
    stage = episode_stage(ep_dir)
    if stage == "empty":
        return None  # ← 把空集數過濾掉
```

新集數沒放錄音 → `stage="empty"` → 過濾掉。

**但流程上不應該感受到這個 bug**
`/api/episode/new` 建立完會 set `holder["ep"]` → 前端 `window.location.href = "/"` → 應該直接進 edit UI，**不該回 dashboard**。

如果使用者真的看到「回 dashboard 但找不到新集數」，代表前端流程有斷點。要驗證的兩個分支：
1. `/api/episode/new` 真的有 set `holder["ep"]` 嗎？（看 api.py:275-318 應該是有）
2. 前端 `createNewEpisode` 真的有跑 `window.location.href = "/"` 嗎？（看 dashboard.js:226 應該是有）
3. 如果都有，可能是 race condition 或 redirect 失效

**建議修法**
1. 先重現問題 + 看實際行為
2. 若確認 redirect 沒生效 → 修前端
3. 順便決定：`_episode_meta` 要不要把 `empty` 也列出來（標示為「⬜ 空集」讓使用者點進去補錄音），這在 P3 review 也提過

**影響檔案**
- `podcast_toolkit/web/dashboard.py:78-79`
- `podcast_toolkit/web/static/dashboard.js:207-229`（createNewEpisode）
- 可能 `podcast_toolkit/web/api.py:275-318`（new endpoint）

---

## 排程建議

3 條的優先序：1 > 2 > 3
- **#1（back-to-dashboard）**：最高優先，現在沒這個基本上 dashboard 無法 dogfood
- **#2（409）**：要先 reproduce + 看 server log 才知道 root cause，可能 30 分鐘也可能 2 小時
- **#3（空集過濾）**：低，等使用者真的抱怨再說

要不要做一個 PR 一次處理，還是各自分 PR，等實際下手前再決定。
