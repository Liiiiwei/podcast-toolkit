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

## 效能：合成編碼（下階段）

- [x] **P1. videotoolbox 硬體編碼 + 硬體解碼** ✅ 2026-06-13 已設為全域預設
  - 實測（M3、37 分雙機集、燒字幕）：libx264 medium 57 分 → vt 10.7 分（**5.4×**），SSIM 0.995 肉眼無差
  - `defaults.yaml encode.video_codec/hwaccel`；assemble 加 `_video_encode_args`/`_hwaccel_args`（vt 自動略過 -preset）
- [x] **P2a. 逐段只跑自己那台的 CPU 濾鏡鏈（crop/scale/字幕）** ✅ 2026-06-14
  - 舊現況：兩台各自「全片」crop/scale + 燒整份字幕 → 再 trim 出 N 段 → 一半是白工
  - 改法：`_multicam_segments` 改成「trim 先切 → crop/scale → 燒字幕 → setpts 歸零 → 規格化」，
    CPU 濾鏡鏈（GPU 解碼搬回 RAM 後的 crop/scale/libass）由 `2×全長` 降到 `1×成品長`
  - 字幕燒在 `setpts=PTS-STARTPTS` 之前（trim 後 PTS 仍主軸，cam B 已先 setpts 對齊）→ 不需逐段位移 SRT
  - 不動 input 結構（仍單一 ffmpeg、單一 filter_complex），不碰 `-ss` → 零 seek 精度風險
  - 驗證：358 unit + 2 個真跑 ffmpeg smoke（YT 5 輸入 / Reels 2 輸入，段落路由 A→B→A 抽幀驗色正確）
  - ⚠ 解碼仍是「兩台全長」——這刀省的是 CPU 濾鏡鏈，不是 GPU 解碼。實際加速幅度待真機量測
    （硬解後若 libass+scale 是主瓶頸 → 接近 2×；若 GPU 解碼/搬運才是瓶頸 → 要再做 P2b）

- [ ] **P2b. 逐段 `-ss` 只解需要的區間（在 P2a 量完、確認解碼仍是瓶頸時才做）**
  - 改法：每段一個 `-ss start -t dur -i camfile` input（fast seek + 精準 trim），decode 也只跑該段
  - 風險點：`-ss` keyframe seek 精度、cam B 的 sync offset 換算、input index 重排（intro/outro/音檔/封面位移）
  - 進一步：分段平行編碼（M3 8 核）+ `-c copy` concat，附帶斷點續跑能力

## 啟動 App（雙擊開介面）

- 已生成 `/Applications/Podcast.app`（本機 osacompile + adhoc 簽章，無 quarantine），雙擊 → 跑 `scripts/podcast-ui.sh` 開 dashboard。
- [ ] **自訂圖示**：預設是 AppleScript applet 灰色圖；之後換成節目 icon（需 `.icns`，套到 `Podcast.app/Contents/Resources/applet.icns` + 重簽 + `touch` app）。
- [ ] **釘 Dock**：之後把 app 拖進 Dock 固定一鍵開。
- 注意：app 把 repo 路徑烤死，搬專案資料夾後要重生成（或重跑 `./install.sh`）。
