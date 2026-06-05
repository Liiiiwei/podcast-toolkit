# Podcast Edit UI 設計文件

- **日期**：2026-05-27
- **作者**：Vincent (透過 Claude Code brainstorming)
- **狀態**：設計確認完成，待轉 implementation plan
- **影響範圍**：`podcast_toolkit/` 新增 `edit` 子指令、`assemble.py` 新增 crop/deletions 支援

---

## 目標

替 `podcast-toolkit` 加上「剪輯前的視覺化編輯介面」，讓使用者在 `resegment` 完字幕後、`assemble` 前，可以在瀏覽器裡完成三件事：

1. **裁切畫框**（構圖修正）— 把錄影時拍到的多餘邊緣裁掉，整支影片套用同一個 16:9 裁切框
2. **點選刪除段落** — 以字幕卡為單位，刪掉錄到的廢話 / 干擾段
3. **直接修改 SRT 錯字** — inline 編輯 `_v2.srt` 的文字內容

工作流位置：
```
init → 放錄音+srt → resegment → [新] edit → assemble
```

---

## 非目標（明確不做）

- 多平台輸出（16:9 / 9:16 / 1:1 多比例同時）
- 動態切鏡頭（誰說話 zoom 到誰）
- 多人協作 / conflict detection
- autosave 暫存草稿
- 細粒度刪除（字 / 句層級；只做字幕卡層級）
- 寫回 `episode.yaml` 的 fixes/card_fixes 自動推導（直接覆寫 `_v2.srt`，最簡）
- 整合進 SKILL.md 自動流程；`podcast edit` 是手動觸發指令

---

## 整體架構

**Form factor：CLI 起本機 web server**

```
podcast edit <path>
   ↓
 啟動 FastAPI（127.0.0.1，隨機 port）
   ↓
 webbrowser.open(http://127.0.0.1:PORT)
   ↓
 使用者在瀏覽器編輯
   ↓
 按「完成並儲存」→ POST /api/save
   ↓
 server 寫檔 → shutdown → CLI 回到 prompt
```

**為什麼選 FastAPI + vanilla HTML/JS（不選 Streamlit/Electron/Tauri）**

- 維護成本最低：純 Python + 三個靜態檔，不引入新生態系
- 完全控制 UI：裁切框拖拉這種互動 Streamlit 做不到
- 無 build step：HTML/JS 改完直接重整瀏覽器
- 跟既有 toolkit 一致：純 Python CLI 風格

**檔案結構（新增）**

```
podcast_toolkit/
├── edit.py              # CLI 入口 + uvicorn 啟動 + webbrowser
├── web/
│   ├── __init__.py
│   ├── api.py           # FastAPI app + 路由
│   └── static/
│       ├── index.html   # SPA
│       ├── app.js
│       └── app.css
```

**前端狀態管理**

- 全部編輯狀態（裁切框座標、刪除清單、修改過的字幕文字）只在 JS memory，不打 API
- 影片用 `<video src="/api/video">` 唯讀串流（Range 分段）
- 「完成並儲存」一次 POST 全部變動 → server 寫檔 + shutdown
- **慣例**：關瀏覽器 = 放棄編輯（無 autosave、無 draft）

---

## UI 版面

單頁面、無頁籤、無 modal。左邊影片預覽與裁切框，右邊字幕卡列表。

```
┌──────────────────────────────────────────────────────────┐
│ 20260601 過嗨乳牛                  [取消]  [完成並儲存]   │
│ 影片 1920×1080 · 字幕卡 47 段 · 已刪 3 · 已修 2          │
├────────────────────────────────┬─────────────────────────┤
│ ┌──────────────────────────┐   │ 00:00-00:04           ✕ │
│ │  ┌──────────────┐        │   │ 大家好歡迎來到我愛上班     │
│ │  │ 橘色裁切框    │ ●●     │   ├─────────────────────────┤
│ │  │ ●          ● │        │   │ 00:04-00:12  [playing]✕ │
│ │  │              │        │   │ 今天要聊的是過嗨乳牛...    │
│ │  │ ●          ● │        │   ├─────────────────────────┤
│ │  └──────────────┘        │   │ 00:12-00:14 [deleted] ↺ │
│ └──────────────────────────┘   │ ~~呃那個~~              │
│ ▶ 00:12 / 47:23  ━━━━━○━━━━   │ ...                     │
│ 裁切：x=6% w=66%  ↺ 重設       │                         │
└────────────────────────────────┴─────────────────────────┘
```

**互動規則**

- **裁切框**：拖四角 handle 縮放、拖框中心移動、「↺ 重設」回到整張。整支影片共用一個框
- **字幕卡點選**：點時間欄 → 影片跳到該段；點文字欄 → inline 編輯（橘色底線即編輯中）
- **刪除字幕卡**：✕ → 卡片變灰、文字劃線、按鈕變 ↺；再點還原
- **正在播放的卡**：自動 highlight 並 scroll 到視窗中央
- **取消按鈕**：顯示確認框「未儲存修改會丟失」→ 確認後關瀏覽器

---

## 後端 API

全部走 `127.0.0.1`，無 auth（純本機）。

| Method & Path | 用途 | 回傳 |
|---|---|---|
| `GET /` | 載入 SPA | `static/index.html` |
| `GET /api/episode` | 讀 episode.yaml + _v2.srt → 初始狀態 | JSON（見下） |
| `GET /api/video` | 串流 main_video（Range 支援） | video/mp4 |
| `POST /api/save` | 寫 episode.yaml crop/deletions + 覆寫 _v2.srt | `{ok: true}` 後 shutdown |
| `POST /api/shutdown` | 取消用（前端關前打一下） | 204 |

**`/api/episode` 回傳結構**（cards 給「全部」段落，前端依 `deletions` 標示哪些是已刪狀態）

```json
{
  "name": "過嗨乳牛",
  "video_duration": 2843.5,
  "video_size": {"width": 1920, "height": 1080},
  "crop": {"x": 0.06, "y": 0.08, "width": 0.66, "height": 0.84},
  "deletions": [2, 4, 7],
  "cards": [
    {"idx": 1, "start": 0.0, "end": 4.2, "text": "大家好歡迎來到我愛上班"},
    {"idx": 2, "start": 4.2, "end": 12.0, "text": "今天要聊的是過嗨乳牛這個議題"}
  ]
}
```

**`/api/save` payload 結構**

```json
{
  "crop": {"x": 0.06, "y": 0.08, "width": 0.66, "height": 0.84},
  "deletions": [2, 4, 7],
  "cards": [{"idx": 5, "text": "蛋白質含量是關鍵"}]
}
```

- `crop`：0-1 浮點比例，存 yaml 時不存 px
- `deletions`：被刪卡片的 `idx` list
- `cards`：只送有改過文字的卡（後端比對覆寫 _v2.srt 對應行）

**啟動細節**

```python
uvicorn.run(app, host="127.0.0.1", port=0)  # port=0 → 系統分配空閒 port
webbrowser.open(f"http://127.0.0.1:{port}")
```

`/api/save` 處理完後 `os.kill(os.getpid(), signal.SIGINT)` 讓 server 自殺。

**沒有的東西**：WebSocket、autosave、auth、user 概念。

---

## 資料模型

### episode.yaml 擴充

```yaml
date: 20260601
name: 過嗨乳牛
main_video: '{name}.mp4'
main_srt: '{name}.srt'
fixes: []
card_fixes: []
force_break: []
force_join: []

# === 新增 ===
crop:                    # 可選，省略 = 不裁切
  x: 0.06               # 0-1 比例
  y: 0.08
  width: 0.66
  height: 0.84
deletions: [2, 4, 7]     # 可選，省略 = 不刪；數字對應 _v2.srt 段落 index (1-based)
```

**設計決策**

- **為什麼存比例不存 px**：影片之後若換解析度（1080p → 4K），比例仍適用，px 不行
- **為什麼 deletions 存 index 不存時間區間**：跟 srt 段落一一對應，直觀；改時間區間要重算
- **resegment 重跑會洗掉 _v2.srt** → deletions 一起失效（合理：斷句變了舊 index 無意義）
  - 這是已知的耦合，使用者重跑 resegment 後需要重新進 edit

### _v2.srt 處理

- 編輯模式只讀 `_v2.srt`（resegment 跑完才會有）
- 「完成並儲存」直接覆寫 `_v2.srt`：用前端送回來的 cards text 重組（時間軸保留、只改文字）
- **沒有備份檔**：要還原跑 `podcast resegment --force` 從 main_srt 重生
- _v2.srt 不存 deletions 資訊（deletions 是 assemble 階段的事，留在 yaml）

### assemble 階段如何使用

- `crop` 不為空 → ffmpeg filter chain 加 `crop=W*0.66:H*0.84:W*0.06:H*0.08`
- `deletions` 不為空 → ffmpeg `select`（或先切片再 concat）跳過刪除段的時間區間
- 字幕同步：burn subtitle 前先把 deletions 對應的字幕段也移除

---

## 錯誤處理

### 啟動階段（CLI 還沒開 server 前）

| 情境 | 處理 |
|---|---|
| `episode.yaml` 不存在 | exit 3「請先 `podcast init`」 |
| `main_video` 找不到 | exit 3「main_video 缺失：<path>」 |
| `_v2.srt` 找不到 | exit 3「請先跑 `podcast resegment`」 |
| port 全被佔（罕見） | exit 1「無法找到空閒 port」 |
| 已有 `podcast edit` 在跑 | lockfile `<集資料夾>/04_工作檔/.edit.lock` 偵測 → exit 1「已有編輯 session 在跑」（內含 pid + port） |

### Runtime（server 運作中）

| 情境 | 處理 |
|---|---|
| yaml 解析失敗 | 500 + 訊息，前端顯示「episode.yaml 格式錯誤」 |
| `_v2.srt` 被外部改動 | 不偵測，以最後 save 寫入版本為準（單人本機） |
| `<video>` 載入失敗 | 前端 console.error + 顯示「影片載入失敗」，仍保留編輯字幕功能 |
| `/api/save` 寫檔失敗 | 500 + 訊息，前端 toast「儲存失敗：<msg>」，server **不**關閉，可重試 |
| 使用者關瀏覽器沒按完成 | server 不偵測，靠 idle timeout 自殺（30 分鐘無請求） |

### 邊界條件

- `deletions` 空 list → assemble 不加 select filter
- `crop` 為 null 或缺欄位 → assemble 不加 crop filter
- 字幕卡全部被刪 → 前端禁用「完成並儲存」按鈕
- 字幕文字編成空字串 → 視同未修改（不寫回）
- 拖裁切框拖出邊界 → 前端 clamp 到 0-1

### 明確不處理

- undo/redo（單次 session 內 ctrl+z 可改文字框內容，但刪除/裁切不做 history）
- conflict detection（單人本機）
- 自動恢復未儲存編輯（關瀏覽器 = 放棄，符合先前確認）

---

## 測試策略

### 單元測試（pytest，跟既有 toolkit 同套）

| 模組 | 測什麼 |
|---|---|
| `web/api.py::save_episode` | mock payload → 驗證 yaml crop/deletions 正確寫入、_v2.srt 正確覆寫 |
| `web/api.py::load_episode` | 範例資料夾 → 驗證回傳 JSON schema 正確 |
| `web/api.py::video_range` | 小 mp4 + `Range: bytes=0-1023` → 驗證 206 + Content-Range |
| `assemble.py` 擴充 | episode.yaml 有 crop → ffmpeg 指令含 `crop=` filter |
| `assemble.py` 擴充 | episode.yaml 有 deletions → ffmpeg 指令含 `select` filter |

### 整合測試

- 擴充 `tests/regression.sh`：跑「過嗨乳牛」+ 一個帶 crop/deletions 的 fixture episode.yaml → 比對輸出 mp4 的解析度、時長、字幕內容
- **不**起 server 跑 E2E：前端是 vanilla JS 沒 build，手動驗

### 手動驗收清單（補進 README）

1. `podcast edit <path>` → 瀏覽器自動開、影片可播
2. 拖裁切框 → console 顯示比例變動
3. 點字幕卡 → 影片跳到對應時間
4. inline 改錯字 → blur 後 highlight 變色
5. ✕ 刪卡 → 變灰、再點 ↺ 還原
6. 「完成並儲存」→ yaml/srt 確實有改、server 關閉、CLI 回到 prompt
7. `podcast assemble` → 輸出 mp4 比例 / 長度 / 字幕都符合編輯

### 覆蓋範圍

- ✅ 核心邏輯（save、load、ffmpeg 指令生成、Range header）
- ✅ 邊界（empty deletions、null crop）
- ❌ 不測前端 DOM（vanilla JS，太脆弱）
- ❌ 不測 server lifecycle（webbrowser.open、shutdown），手動驗

---

## 依賴新增

`requirements.txt` 新增：

```
fastapi>=0.110
uvicorn[standard]>=0.27
```

PyYAML 已有，不需新增。`install.sh` 跟著加 `pip3 install fastapi uvicorn[standard]`。

---

## CLI 變更摘要

`cli.py` 新增第五個指令：

```python
pe = sub.add_parser("edit", help="在瀏覽器編輯：裁切 / 刪段 / 改字")
pe.add_argument("path", nargs="?", default=".", help="集資料夾路徑（預設：當前目錄）")
pe.set_defaults(func=cmd_edit)
```

README 工作流新增第 5.5 步：
```bash
# 5.5 (可選) 視覺化編輯：裁切畫框 / 刪段 / 改字
podcast edit "$HOME/Downloads/20260601 新集名"
```

---

## Open Questions（轉 implementation plan 前可決，也可延後）

無。所有需求面決策已在 brainstorm 完成。
