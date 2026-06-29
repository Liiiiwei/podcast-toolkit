# UI Design Audit — vs/edit-ui

**Date:** 2026-06-05
**Scope:** dashboard.html + index.html（edit UI）+ 三個 modal（cam / settings / 字幕卡）
**Method:** Playwright 截 desktop 1440 / tablet 768 / mobile 375，配 CSS 原始碼比對
**Out of scope:** 文案內容、影音演算法、後端 API 行為
**截圖位置:** `design-review/01..07-*.png`

範圍紀律：本份**只列發現 + 建議改法**，不下手。使用者要求「最後一次做」，所以這份 = todo backlog。

---

## 嚴重度說明

- **P1 (HIGH)** = 影響可用性，使用者會卡住或誤操作
- **P2 (MEDIUM)** = 影響觀感與信任，不至於壞功能
- **P3 (LOW)** = polish，加分不加分都行

---

## P1 #1 — Dashboard ↔ Edit UI 是兩套設計系統

**現象**
- Dashboard (`dashboard.css`)：白底 `#f5f5f7`、Apple 風 `BlinkMacSystemFont`、藍色 `#007aff` accent、border-radius 6-12px
- Edit UI (`app.css`)：深色 `#1a1a1a`、`Helvetica Neue / Noto Sans TC`、橘色 `#ff6b35` accent、border-radius 3-4px

點 episode card 進到 edit UI 像進到另一個 app。Topbar 從白色 20px 標題 → 深灰 16px 標題；按鈕從圓潤白底 → 方正深色；連 hover 顏色都從藍變橘。

**為何重要**
工具感不連貫 = 使用者每次切換都要重新建立心智模型；對外展示／錄影教學會被質疑「這不是同一個產品吧」。

**建議**
擇一 design language，建議統一為 **edit UI 的深色橘**（工作場景時間長、護眼、是主要工作畫面）。
1. `dashboard.css` 重寫成深色版（背景 `#1a1a1a`、accent `#ff6b35`、字體與 edit UI 同步）
2. 抽公用 tokens 到 `tokens.css`：`--bg`, `--surface`, `--text`, `--text-dim`, `--accent`, `--radius-sm`, `--radius-md`
3. `dashboard.css` / `app.css` 都引用 tokens

**影響檔案**
- `podcast_toolkit/web/static/dashboard.css`（重寫）
- `podcast_toolkit/web/static/app.css:6-14`（抽 tokens）
- 新增 `podcast_toolkit/web/static/tokens.css`

---

## P1 #2 — Mobile / Tablet 整個 layout 爆炸

**現象**
375px viewport 下整頁高度 ≈ **37,000px**（一張 desktop 螢幕的 41 倍）。原因：

```css
/* app.css:1126-1129 */
@media (max-width: 900px) {
  .cards-pane {
    overflow: visible;
    max-height: none;
  }
}
```

≤900px 時 cards-pane（569 張字幕卡的清單）拿掉 overflow → 整列攤平。同個 viewport 下還有 video + 裁切控制 + trim + typo-pane 全部疊著，使用者要捲 30 分鐘才能看到底部。

**為何重要**
雖然 edit 場景 80% 是桌機，但：
- iPad 是常見預覽場景 → 1024 寬還沒事，768 直立就爆
- 路上想用手機 quick review → 完全不可用

**建議**
保留 `cards-pane` 的 scroll container，只把 grid 從 `1fr 400px` 改成 `1fr`，並把 cards-pane 高度限制成 `60vh`：

```css
@media (max-width: 900px) {
  .body { grid-template-columns: 1fr; }
  .cards-pane {
    /* overflow-y: auto 保留（不要寫成 visible） */
    max-height: 60vh;
  }
}
```

**影響檔案**
- `podcast_toolkit/web/static/app.css:1105-1145`（修 mobile breakpoint）

---

## P1 #3 — 「自動對齊」沒進度，看起來卡住

**現象**
按下「🎯 一鍵全部對齊（cam B + 音檔）」/ 「🎯 自動對齊」後，按鈕只變成「計算中…」純文字 disabled，**沒有 spinner、沒有時間、沒有階段提示**。

實際 backend 工作：
1. ffmpeg 從 cam A 抽 120 秒 PCM
2. ffmpeg 從 cam B / 音檔抽 120 秒 PCM
3. `numpy.correlate` 互相關運算

整段在桌機通常 30 秒 - 2 分鐘，**音檔大或硬碟慢時可能 3-5 分鐘**。期間瀏覽器毫無動靜 → 使用者以為當機，會去按取消或刷新（中斷工作）。

**為何重要**
這是 cam align 流程的**主動作**，使用者每集都會按一次。每次都「不知道還要等多久」= 累積成「這 app 卡卡的」印象，而且實際上有人會誤判成卡住直接 kill。

**建議**
分兩階段做，**第一階段只動前端**（30 分鐘搞定）：

1. **CSS spinner 動畫** — 按鈕加 `.btn-loading` class，左側塞 `<span class="spinner"></span>`（純 CSS keyframes 旋轉）
2. **Elapsed timer** — `setInterval` 每秒更新按鈕文字：「計算中… 0:23」
3. **預期時間提示** — modal 底部加灰字「自動對齊通常需要 30 秒 - 3 分鐘，請耐心等候」（管理期待）

```js
btn.classList.add("loading");
const t0 = performance.now();
const tick = setInterval(() => {
  const sec = Math.floor((performance.now() - t0) / 1000);
  const mm = Math.floor(sec / 60);
  const ss = String(sec % 60).padStart(2, "0");
  btn.textContent = `計算中… ${mm}:${ss}`;
}, 1000);
// ... fetch ...
clearInterval(tick);
btn.classList.remove("loading");
btn.textContent = "🎯 一鍵全部對齊（cam B + 音檔）";
```

```css
.spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-right: 6px;
  vertical-align: middle;
}
@keyframes spin { to { transform: rotate(360deg); } }
```

**第二階段（選做，需動 backend）**：真實階段進度
- 改 `/api/auto-align` 成 SSE endpoint，backend `yield` 三階段：`{"phase": "extract_cam_a"}` → `extract_cam_b` → `correlate`
- 前端按 phase 更新文字「抽取 cam A 音訊…」「抽取 cam B 音訊…」「互相關計算…」
- 投入 1-2 小時，但體驗會從「等待」變成「我知道它在做什麼」

**短期只做第一階段就夠**，第二階段等真的有人抱怨再做。

**影響檔案**
- `podcast_toolkit/web/static/app.js:2039-2127`（三個 align handler：`#cam-auto-align`、`#align-all`、`#audio-auto-align`，都同步改）
- `podcast_toolkit/web/static/app.css`（新增 `.spinner` + `@keyframes spin`）
- `podcast_toolkit/web/static/index.html`（cam modal 底部加預期時間提示）

第二階段：
- `podcast_toolkit/web/api.py`（`/api/auto-align` 改 SSE）
- `podcast_toolkit/audio_align.py`（auto_align 改 generator yield phase）

---

## P1 #4 — Edit UI Topbar 11 個控件擠一排

**現象**
頂部一行塞：標題 + 狀態 + 「← 列表」+ 「📁 開啟集資料夾」+ 「📅 新建集」+ 「⚙」+ 「🎥 鏡頭」+ 「🎬 合成 YT」+ 「📱 合成 Reels」+ 「取消」+ 「完成並儲存」=  **11 個操作**。

桌機 1440px 還勉強；1280 以下會擠到第二行或截斷。視覺重點完全分散：「完成並儲存」（主要 CTA）跟「取消」並列在右上，但同時還有「合成 YT / Reels」（次主要 CTA）—— 使用者要分辨「我現在該按哪個」。

**為何重要**
工作流是有順序的：編輯 → 儲存 → 合成 → 完成。現在所有按鈕都在同一階級，新手很容易誤按。

**建議**
分組重排：
1. **左側（context）**：標題 + 狀態 + 「← 列表」
2. **中間（檔案層級）**：「📁 開啟資料夾」「📅 新建集」
3. **右側（編輯動作）**：「⚙」「🎥 鏡頭」「取消」「完成並儲存」
4. **「🎬 合成 YT / 📱 合成 Reels」搬到 video pane 下方專用合成區**（合成是 edit 完成後的動作，不該跟編輯按鈕競爭）

可選：1280px 以下把「📁 開啟資料夾」「📅 新建集」收進「⚙」下拉。

**影響檔案**
- `podcast_toolkit/web/static/index.html`（topbar 結構）
- `podcast_toolkit/web/static/app.css:33-101`（topbar / actions）

---

## P2 #5 — 配色對比邊緣（WCAG AA 風險）

**現象**
- `.status { color: #888 }` on `#222` topbar → contrast ratio ≈ **3.5:1**，AA fail（需 4.5:1）
- `.typo-sub { color: #666 }` on `#0a0a0a` typo-pane → contrast ≈ **3.3:1**，AA fail
- `.crop-ratios .ratio-btn { color: #888 }` on `#1a1a1a` → contrast ≈ **4.4:1**，邊緣

**為何重要**
小字（11-12px）需要 4.5:1。`#888` 給次要資訊看起來「對」但實際在某些光線下會消失，年紀大或螢幕反光的使用者直接看不到。

**建議**
- 次要文字統一升到 `#a0a0a0`（≈ 6.1:1，AA pass）
- 真的要「淡」的用 `#9a9a9a`（5.6:1）
- 之後抽到 token `--text-dim: #a0a0a0`

**影響檔案**
- `podcast_toolkit/web/static/app.css` — 全域搜 `color: #888`、`color: #666`、`color: #aaa` 替換

---

## P2 #6 — Modal 實作兩套，行為不一致

**現象**
- Dashboard 用原生 `<dialog>` + `::backdrop`（`dashboard.css:132-176`）→ 免費獲得 ESC 關閉、focus trap、backdrop click
- Edit UI 用自製 `.modal-backdrop`（`app.css:681-768`）→ 沒有 ESC 關、沒有 focus trap、backdrop click 行為要手寫

實測：cam modal 開啟後按 ESC 不會關（dashboard 的 new-ep modal 會）。

**為何重要**
keyboard 使用者切不出 modal → 累積成「這 app 操作很卡」的感覺。

**建議**
edit UI modal 全部改用 `<dialog>` element。改動小（HTML 改 `<div class="modal-backdrop">` → `<dialog class="modal">`、JS 改 `classList.toggle` → `.showModal()` / `.close()`），但行為一致 + 免費 a11y。

**影響檔案**
- `podcast_toolkit/web/static/index.html`（3 個 modal 結構）
- `podcast_toolkit/web/static/app.js`（modal 開關邏輯）
- `podcast_toolkit/web/static/app.css:681-768`

---

## P2 #7 — Spacing 沒有 scale，看起來凌亂

**現象**
`app.css` 裡 `padding` / `gap` / `margin` 用了 `2px, 3px, 4px, 6px, 8px, 10px, 12px, 14px, 16px` 全部混用，沒有規律。例：

- `.topbar { gap: 8px; padding: 10px 16px }`
- `.controls { gap: 10px; padding: 8px 0 }`
- `.crop-ratios { gap: 6px }`
- `.typo-pane { gap: 8px; padding: 8px 10px }`

每個區塊都「差一點點」，眼睛抓不到對齊基線。

**為何重要**
熟手用幾天會覺得「為什麼這裡看起來不對」但講不出來 — 就是 spacing 沒系統。

**建議**
建立 4 / 8 / 12 / 16 / 24 scale（捨棄 2 / 3 / 6 / 10 / 14），抽到 tokens：

```css
--space-xs: 4px;
--space-sm: 8px;
--space-md: 12px;
--space-lg: 16px;
--space-xl: 24px;
```

全域 sweep 一次替換。

**影響檔案**
- `podcast_toolkit/web/static/tokens.css`（新建，跟 P1 #1 一起）
- `podcast_toolkit/web/static/app.css`（全文 sweep）

---

## P2 #8 — Cam modal 警告文字過長、層級不對

**現象**
cam modal 底部有一段紅字警告：

> ⚠️ 目前對齊資料只存進 episode.yaml，assemble 還沒接通

這是**開發狀態**而非使用者該知道的事，現在直接面對使用者展示，會讓人懷疑「那我按了到底有沒有用？」。

**為何重要**
信任感破壞。產品還沒 ready 的部分應該藏在 dev mode 或 commit message 裡，不是 UI 文字。

**建議**
1. **短期**：改成中性提示「對齊偏移會儲存到 episode.yaml，後續可在 assemble 階段套用」
2. **長期**：把 assemble 接通（這本來就是 `docs/superpowers/plans/2026-06-04-fully-web-ui.md` 的工作）後拿掉這條提示

**影響檔案**
- `podcast_toolkit/web/static/index.html`（cam modal 警告文案）

---

## P2 #9 — Topbar 11 個 emoji 視覺噪音

**現象**
頂部按鈕全部開頭加 emoji：📁 📅 ⚙ 🎥 🎬 📱。看起來活潑但 11 個排一排 = emoji 牆。

**為何重要**
emoji 本來是區隔不同類型的 visual cue，現在全部都用 → 反而失去區隔功能；而且 emoji 在不同 OS / 字體下表現差很大（Mac vs Win vs Linux）。

**建議**
擇一：
- A. **全拿掉 emoji**，只留文字（看起來更工具感，符合 edit UI 深色終端風）
- B. **只主要 CTA 留 emoji**：合成（🎬）、完成（無 emoji 但用色彩區分）

推薦 A。如果想保留視覺重點，用 SVG icon（heroicons / lucide），統一 stroke style。

**影響檔案**
- `podcast_toolkit/web/static/index.html`（topbar buttons）

---

## P3 #10 — Episode badge emoji + 顏色雙重

**現象**
Dashboard 卡片右側顯示「🟡 未合成」— emoji + 黃色 pill 重複表達同件事。

**為何重要**
小問題，但 emoji 在某些字體下會跟 pill 背景色衝突（emoji 自帶顏色）。

**建議**
拿掉 emoji，只用 pill 顏色 + 文字：「未合成」（黃 bg）/「已合成」（綠 bg）/「需轉錄」（灰 bg）

**影響檔案**
- `podcast_toolkit/web/static/dashboard.js`（episode card 渲染）
- `podcast_toolkit/web/static/dashboard.css:110-131`（stage-* 樣式已有，不用改）

---

## P3 #11 — 缺 favicon

**現象**
Console error：`GET /favicon.ico 404`。每次開頁面都噴一條 error log。

**為何重要**
小事，但分頁標籤顯示空白方塊 + 干擾 console 看其他真正的 error。

**建議**
放一個 `favicon.ico`（任何能代表的 emoji 轉檔，例如 🎙️），讓 FastAPI static serve 就好。

**影響檔案**
- 新增 `podcast_toolkit/web/static/favicon.ico`
- `podcast_toolkit/web/api.py`（如果 static mount 不含 favicon route，加一個）

---

## P3 #12 — 字幕卡清單沒有 virtualization

**現象**
569 張字幕卡全部 render 在 DOM 裡。打開 edit UI 初次載入會看到明顯的 hang（瀏覽器 layout 569 個 row）。

**為何重要**
集數變長（90 分鐘 podcast 可能 1000+ 卡）會線性變慢；雖然不到不可用，但「打開就要等」會累積成「這 app 慢」。

**建議**
**先量再改**。Chrome devtools Performance tab 錄一次「載入 → 第一次互動」，看：
1. JS 執行 < 200ms？OK 不用動
2. 200-500ms？考慮 `content-visibility: auto` 加每張卡上（CSS 一行解決）
3. > 500ms？才考慮 virtual scrolling（投入產出比差，自己寫容易出 bug）

**影響檔案**
- 量測：Chrome devtools，無檔案改動
- 若決定改：`podcast_toolkit/web/static/app.css`（加 `.card { content-visibility: auto }`）

---

## P3 #13 — Settings modal 的 API key 用 `<input type="password">` 但沒包 `<form>`

**現象**
Console 噴 `[DOM] Password field is not contained in a form`。功能上沒壞，但 Chrome accessibility hint。

**為何重要**
小事。但 password manager 可能不知道該不該存。

**建議**
用 `<form>` 包起來，加 `onsubmit="return false"` 阻止實際送出（保留現有 click handler）。

**影響檔案**
- `podcast_toolkit/web/static/index.html`（settings modal）

---

## 排程建議（落地順序）

如果一次做完，建議順序：

1. **先做 P1 #3（自動對齊進度）** — 純前端 spinner + timer，30 分鐘，立竿見影
2. **再做 P1 #2（mobile breakpoint）** — 純 CSS，30 分鐘
3. **P1 #1 + P2 #7（tokens + spacing scale）** — 抽 tokens.css 是基礎工程，後面所有 P2/P3 都會受益，2-3 小時
4. **P1 #4（topbar 重排）** — HTML 結構動，1 小時
5. **P2 #6（modal 換 `<dialog>`）** — HTML + JS 雙改，1-2 小時
6. **P2 #5（對比修正）** — 全文 sweep，30 分鐘（建議在做完 tokens 後一起）
7. **P2 #8 + #9 + P3 系列** — 各 5-15 分鐘的 polish

**預估總時間：5-9 小時**（單人專注）

**注意事項**
- 動 tokens 那一刻會 break 所有畫面 — 一定要分 commit 而且每個 commit 都能跑（不要一個 commit 改全部）
- mobile breakpoint 修完之後，記得實機（iPhone Safari）開來驗證一次，不要只靠 devtools mobile mode
- 完成宣告附 before/after 截圖（每個 P1 一張）

---

## 不在這份 audit 範圍

這些之前已經有 issue 或在別份計畫裡：

- ✅ Back-to-dashboard 按鈕（`docs/review/2026-06-04-dashboard-followups.md` #1，已完成）
- 409 errors on edit UI（同 followups #2，仍 pending）
- 新建集數立刻被 dashboard 過濾掉（同 followups #3，pending）
- assemble.py 接通 audio sync offset（`docs/superpowers/plans/2026-06-04-fully-web-ui.md`）
