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

## 2. Edit UI 在某些 episode 狀態下回 409 ✅ resolved（2026-06-05 驗證）

**原始現象**（已過時）
從 dashboard 點 card 開啟某些 episode 後，edit UI 載入 `app.js`：
```
GET /api/video 409 (Conflict)
GET /api/episode 409 (Conflict)
GET /api/files 409 (Conflict)
Error: /api/episode HTTP 409  at loadEpisodeState (app.js:510:20)
```

**根因**
409 全部來自 `api.py:166` 的 `_require_ep()` — 當 `holder["ep"] is None` 時所有需要選集的 endpoint 都會 raise。當時的觸發路徑：
1. Server 啟動後 `holder["ep"]` 為 None（dashboard 模式）
2. 使用者點 card → `/api/episodes/open` set holder → `window.location.href = "/"`
3. 但瀏覽器吃舊 cache 的 index.html（沒設 no-store），或 redirect 慢一拍
4. app.js 觸發 /api/episode 等 → 409

**已修（post-2026-06-04）**
- `5264446 fix(dashboard): / 端點加 Cache-Control: no-store` — / 不再被瀏覽器靜默 cache，dashboard ↔ edit UI 切換永遠打到 server
- `a6d228d` 的 `loadEpisodeState`：偵測到 409 → `window.location.href = "/"` 自我修復 → server 因 holder=None 改 serve `dashboard.html`
- `a6d228d` 的 `load().catch`：吞掉「尚未選集」error，不 flash 紅色錯誤畫面
- `/api/files` 用 `if (!r.ok) return;` 安靜失敗
- `/api/video` 設給 `<video>.src` 直接吃 409，瀏覽器只 emit error event 不 throw

**2026-06-05 curl 驗證**
```
POST /api/episodes/close → holder=None
GET / → text/html，title=Podcast Toolkit（dashboard.html）
GET /api/episode → 409 {"detail":"尚未選集..."}
GET /api/video → 409
GET /api/files → 409
```
前端流程：loadEpisodeState 看到 409 → location.href="/" → server 回 dashboard.html → 使用者落地 dashboard，無錯誤畫面。

**剩下的 console 噪音**：/api/video 和 /api/files 平行 fire 時仍會在 DevTools network panel 留下 409 紀錄。屬無害紀錄，不影響 UX。若未來想根除，可在 `loadFiles` 開頭 peek `window.location.pathname` 或加一個 holder-status probe，但目前 not worth it。

---

## 3. 新建集數立刻被 dashboard 過濾掉 ✅ resolved（2026-06-05）

**現象**
按「📅 新建集」建立 `20260604 test` 後，若使用者按 back-to-dashboard 想看 list，看不到剛建的集數。

**根因**
`podcast_toolkit/web/dashboard.py:78-79` 把 `stage="empty"` 過濾掉。新集沒放錄音 → empty → 從 list 消失。

**正常流程不會踩到**：`/api/episode/new` set holder + 前端 `window.location.href = "/"` 會直接進 edit UI。只有當使用者再從 edit UI 按 back-to-dashboard 時才會發現「集不見了」。

**修法（2026-06-05）**
直接移除 `_episode_meta` 的 `stage == "empty"` 過濾。dashboard 已有 `STAGE_LABEL.empty = "空集"` 與 `.stage-empty` CSS（faint gray），點進去 edit UI 的 `needs_transcribe`/empty-state flow 已可處理空集。讓 dashboard 顯示空集 = 使用者能找回剛建好還沒錄的集數，亦能單擊回到 edit UI 補錄音檔。

**影響檔案**
- `podcast_toolkit/web/dashboard.py`（移除 empty 過濾）

---

## 排程建議

3 條皆已處理（2026-06-05 收尾）：
- **#1（back-to-dashboard）**：commit `675f295`
- **#2（409）**：post-2026-06-04 已自我修復（commits `5264446` + `a6d228d`），本日 curl 驗證通過
- **#3（空集過濾）**：本日移除 `_episode_meta` empty 過濾
