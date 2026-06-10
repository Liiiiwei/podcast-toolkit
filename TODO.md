# TODO

從 2026-06-06 `/review` 報告搬下來的修補項，按優先級排列。

## 抽屜（drawer）a11y

- [x] **A1. 補 `aria-controls` 與 `role="tabpanel"`** ✅ 2026-06-06 commit 34ec794
  - `podcast_toolkit/web/static/index.html:252-310`
  - `.drawer-tab` 加 `aria-controls="drawer-pane-{name}"` 與 `id="drawer-tab-{name}"`
  - `.drawer-pane` 加 `role="tabpanel"` + `aria-labelledby="drawer-tab-{name}"` + `tabindex="0"`

- [x] **A2. `#drawer-toggle` 移出 `role="tablist"`** ✅ 2026-06-06 commit 34ec794
  - `podcast_toolkit/web/static/index.html:253, 276-284`
  - 用 `.drawer-header` flex 容器包住 tablist + toggle 為兄弟節點，padding/border-bottom 上移

- [x] **A3. 抽屜分頁加 ArrowLeft / ArrowRight / Home / End 鍵盤切換** ✅ 2026-06-06 commit 34ec794
  - `podcast_toolkit/web/static/app.js` setupDrawer 內 keydown handler
  - 含 roving tabindex（active=0、非 active=-1）+ focus 跟隨

## 響應式

- [x] **A4. `@media (max-width: 900px)` 沒同步新的 `.body` / `.body-top` grid** ✅ 2026-06-06 commit 34ec794
  - `podcast_toolkit/web/static/app.css` @media 補 `.body { grid-template-rows: auto auto; --drawer-h: auto; }` 與 `.body-top { grid-template-columns: 1fr; overflow: visible; }`
  - `.drawer` 改 `height: auto; max-height: 60vh`

## 程式碼註解

- [x] **A5. `transcribe.py:22` 註解誤導** ✅ 2026-06-06 commit 34ec794
  - 改成 `# OpenAI Whisper-1：/v1/audio/transcriptions verbose_json + word timestamps；prompt 欄接受 224 token 的詞庫提詞偏值`

## UI polish

- [x] **A6. 抽屜 count pill 在 0 時別顯示** ✅ 2026-06-06 commit 34ec794
  - CSS 補 `.drawer-tab-count:empty { display: none; }`
  - JS renderTypo / renderFiles 改 `n > 0 ? String(n) : ""`

## 觀察項（暫不動，列為背景）

- `subtitle_style` 與 `subtitle_style_reels` 同時設 outline + shadow，DESIGN.md A1 反 pattern 邊緣案例，但燒字幕需要對比，可接受
- `assemble._write_ass_from_srt` 不清舊 `.ass`，work_dir 失敗會被外層整包清，影響極低

## 待驗證（A1-A6 落地後）

- [ ] **V1. 跑 dev server 開瀏覽器人工驗證 A1-A6**
  - 抽屜 Tab 鍵跳進去、ArrowLeft/Right 切分頁、Home/End 跳首尾
  - VoiceOver 念 tab/tabpanel 角色正確、不再把 toggle 當第 3 個 tab
  - 縮窗 ≤900px 看 cards-pane 是否正確堆到下方、影片不被擠
  - 抽屜 count pill 在 0 typo / 0 file 時消失
