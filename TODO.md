# TODO

從 2026-06-06 `/review` 報告搬下來的修補項，按優先級排列。

## 自動化後製管線（feat/auto-pipeline）

目標：使用者選好「影片 + 字幕」後，盡量自動把後製做完，只剩檢查 + 輸出。

- [x] **AP1. 字幕語意校對引擎** ✅ `podcast proofread`
  - `proofread.py`：provider 抽象（claude_code 本地 / gemini API / off），`auto` 解析（claude CLI 在→本地；否則 gemini key；否則跳過 → **非 CC 使用者零影響**）
  - 四條規則（同音錯字 / 專名詞庫 / 子句空格 / 去填充詞）+ 安全閘（QA：擋掉短卡被換長句的捏造）
  - 分塊呼叫 `claude -p --output-format json`、只改文字、先備份 `.pre-proofread.bak`
  - **效能/穩定（沈奕妤 1235 卡實跑暴露）**：
    - 分塊**並行**（`ThreadPoolExecutor`，`max_workers`）— 每塊在等模型回應，並行把牆鐘時間壓掉數倍（~20 分→~3 分）
    - **跳過已刪卡**（不浪費模型時間校對等下要砍的；本集 861/1235）
    - **單塊失敗不拖垮全部**（逾時/壞 JSON → 記下、套其餘成功塊）
    - `--model` 旗標（bulk 校對用 sonnet 比 Opus 預設快很多）；defaults 補 `max_workers: 4`
  - 實測沈奕妤：修 118 卡（去 嗯/啊/呃/哎 填充詞、简→繁 輕松→輕鬆/復制→複製、詞庫），QA 還原 0
- [ ] **AP2. 自動鏡頭對應**（分軌集）：speakers.json（idx→speaker）經 speaker→camera 映射推出 cameras_mapping；episode.yaml 可設 `speaker_camera_mapping` override
  - **單軌集不適用**（沒有分軌講者資料）；`auto.py._run_camera` 已留 hook：偵到 speakers.json 才 import `autocamera` 跑，否則優雅略過
- [x] **AP3. 自動去頭去尾** ✅ `autotrim.py`
  - `silencedetect.py` 補 `parse_duration` / `parse_tail_silence` / `detect_tail_silence`（解析 ffmpeg Duration + 尾段一路靜音到檔尾）
  - 只補「沒設過」的 head/tail_trim_sec（`force` 才重測覆寫），safe round-trip 寫回 episode.yaml（保留 deletions 等欄位）
  - **`-vn` 修正**：silencedetect 是 audio filter，但沒加 -vn 時 ffmpeg 仍把整段 4K 視訊解碼丟 null（36 分片數分鐘白工）→ 加 `-vn` 只解音訊，降到 ~5 秒。**連帶修好 UI 智慧建議 trim head 在大檔上很慢的問題**
  - 沈奕妤實測：head 42.7 手動值保留、尾段偵測為 0（內容到片尾，無尾可去）
- [x] **AP4. 編排** ✅ `podcast auto <集>`（串 AP1→AP2→AP3，`--no-proofread/--no-camera/--no-trim`、`--provider`、`--force`）
  - [ ] Web「✨ 一鍵自動」背景 job + 進度條（下一步）

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
- [ ] **P2. 只解需要的段落（結構性優化，預估再快 ~2×：10.7 分 → 約 6 分）**
  - 現況：兩台攝影機各自「全片」解碼 + 各自燒整份字幕，再 trim 出 61 段 → 大量白工
  - 改法：每段用 `-ss/-t` 只解需要的區間（或預先 per-segment 切小檔），字幕燒製對齊各段時間軸
  - 風險點：`-ss` keyframe seek 精度、cam B 的 sync offset、燒字幕時間軸換算、淡入淡出邊界
  - 進一步：分段平行編碼（M3 8 核）+ `-c copy` concat，附帶斷點續跑能力

## 啟動 App（雙擊開介面）

- 已生成 `/Applications/Podcast.app`（本機 osacompile + adhoc 簽章，無 quarantine），雙擊 → 跑 `scripts/podcast-ui.sh` 開 dashboard。
- [ ] **自訂圖示**：預設是 AppleScript applet 灰色圖；之後換成節目 icon（需 `.icns`，套到 `Podcast.app/Contents/Resources/applet.icns` + 重簽 + `touch` app）。
- [ ] **釘 Dock**：之後把 app 拖進 Dock 固定一鍵開。
- 注意：app 把 repo 路徑烤死，搬專案資料夾後要重生成（或重跑 `./install.sh`）。
