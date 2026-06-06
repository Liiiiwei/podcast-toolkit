# TODO

從 2026-06-06 `/review` 報告搬下來的修補項，按優先級排列。

## 抽屜（drawer）a11y

- [ ] **A1. 補 `aria-controls` 與 `role="tabpanel"`**
  - `podcast_toolkit/web/static/index.html:252-310`
  - `.drawer-tab` 加 `aria-controls="drawer-pane-{name}"` 與 `id="drawer-tab-{name}"`
  - `.drawer-pane` 加 `role="tabpanel"` + `aria-labelledby="drawer-tab-{name}"` + `tabindex="0"`

- [ ] **A2. `#drawer-toggle` 移出 `role="tablist"`**
  - `podcast_toolkit/web/static/index.html:253, 276-284`
  - 現在收合按鈕在 tablist 內，螢幕閱讀器會當成第 3 個 tab
  - 解法：把 `#drawer-toggle` 包成獨立節點放在 `.drawer-tabs` 外，用 flex 對齊保持視覺位置

- [ ] **A3. 抽屜分頁加 ArrowLeft / ArrowRight / Home / End 鍵盤切換**
  - `podcast_toolkit/web/static/app.js:1230-1241`
  - WAI-ARIA tab pattern 標準鍵盤導覽
  - 在 `setupDrawer()` 內補 `keydown` handler，切完要 `tabs[next].focus()`

## 響應式

- [ ] **A4. `@media (max-width: 900px)` 沒同步新的 `.body` / `.body-top` grid**
  - `podcast_toolkit/web/static/app.css:1499-1525`
  - `.body` 現用 `grid-template-rows: minmax(0,1fr) auto` + `--drawer-h: 32vh`；`.body-top` 用 `1fr 420px`
  - ≤900px 應該補：`.body-top { grid-template-columns: 1fr; }` 與調小 `--drawer-h`（或設 auto）
  - 手機上目前 cards-pane 420px 固定欄會擠掉影片

## 程式碼註解

- [ ] **A5. `transcribe.py:22` 註解誤導**
  - 現註解寫「用 chat completions audio modality；唯一支援 prompt-injected glossary」
  - 實際跑的是 `/v1/audio/transcriptions verbose_json + word_timestamps`，且 xAI/Gemini 都吃 prompt
  - 改成：`# OpenAI Whisper-1：/v1/audio/transcriptions verbose_json + word timestamps，prompt 欄注入 glossary 提詞（224 token 上限）`

## UI polish

- [ ] **A6. 抽屜 count pill 在 0 時別顯示**
  - `podcast_toolkit/web/static/app.css:1777-1786`
  - 空狀態下會看到灰色「0」pill，視覺噪音
  - CSS 補 `.drawer-tab-count:empty { display: none; }`，JS 端 0 改塞 `""`

## 觀察項（暫不動，列為背景）

- `subtitle_style` 與 `subtitle_style_reels` 同時設 outline + shadow，DESIGN.md A1 反 pattern 邊緣案例，但燒字幕需要對比，可接受
- `assemble._write_ass_from_srt` 不清舊 `.ass`，work_dir 失敗會被外層整包清，影響極低
