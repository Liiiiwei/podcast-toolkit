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
- [x] **AP2. 自動鏡頭對應** ✅ `cameras_suggest.py`（`podcast suggest-cameras`，PR#2 已有）+ 接進 `podcast auto`
  - 規則式：home 鏡頭待著、`feature` 講者連講 ≥`min_sec` 才切到他的鏡頭、講完回 home（`defaults camera_rule`）
  - 產出**時間版** cameras.json v2（`transitions:[{t,cam}]`，與字幕脫鉤）；單軌集無 speakers.json 自動略過
  - `auto._run_camera` 已從 phantom `autocamera` 改接 `cameras_suggest.run(ep, force)`
  - **資料來源**：分軌 `merge-per-mic` 或 **Breeze `ingest-breeze`** 都會產 speakers.json → 直接餵這步
- [x] **AP5. Breeze ASR 匯入** ✅ `ingest_breeze.py`（`podcast ingest-breeze`）
  - Breeze（本地 Whisper-large-v2 微調，台灣腔 + 中英 + 逐字時間 + jieba 斷句 + 麥能量講者標）
  - 解析含講者 `[MicN]` SRT → 去標籤寫 `_final_v2.srt` + 拆 MicN→speaker 寫 `speakers.json`
  - 沈奕妤實測：816 卡 + 3 講者 → suggest-cameras 自動推 58 個切點；時間版鏡頭不受換字幕影響
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
  - ⚠ **2026-08-05 發現上面這條 `height: auto` 從沒生效過，比原記載更嚴重** ✅ commit `0b53e05`：
    `.drawer` 基礎規則 `height: 32vh` 整條蓋掉前段的 `@media` 區塊（`@media` 不加 specificity 權重，純比誰在後面），
    窄螢幕從來沒有例外過；修好順序後才發現 `height: auto` 本身也不成立 —— `.drawer-pane` 是 `position: absolute`
    脫離常規流，`.drawer-panes` 內在高度恆為 0，`auto` 會讓抽屜塌成只剩標題列（實測 45px）。最終改用固定 `height: 40vh`。

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
  - 驗證：unit + 2 個真跑 ffmpeg smoke（YT 5 輸入 / Reels 2 輸入，段落路由 A→B→A 抽幀驗色正確）
  - 真機量測（過嗨乳牛3，1080p 雙機、含旋轉/封面/1.25 倍速/外接音）：**70 分 → 40 分（~1.75×）**

- [x] **量測：定位 40 分的真兇** ✅ 2026-06-14（過嗨乳牛3 母帶實測，60s benchmark）
  - 解碼**不是**瓶頸：1080p H.264/HEVC 硬解+搬回 RAM **4.56–4.67×**；編碼 h264_vt **3.92×**；libass 燒字幕 **3×**
  - **真兇 = `rotate`（旋轉拉正）**：cam B 加 rotate 從 3.84× 崩到 **0.705×**，佔一半時間軸 → 吃掉 ~21 分
  - rotate 又**難平行**：4 並行每個剩 0.28×（聚合 1.12×），全核榨頂 ~1.9× → 分段平行救不了
  - ⇒ 原本想的 P2b「逐段 `-ss` 解碼」**不必做**（解碼本來就快）；nearest（bilinear=0）只到 1.05×，治標

- [x] **P2c. 旋轉拉正預烤 + 快取** ✅ 2026-06-14
  - 有角度的鏡頭先一次性 `rotate=angle:ow=iw:oh=ih` 轉正成 proxy（黑角保留交給後續 crop），
    assemble 改吃 proxy、該鏡頭 rotate 設 0 → 主合成跑無 rotate 的 ~3×（每集約 10–12 分）
  - 快取鍵 = 角度 + 來源檔簽章（mtime/size）；YT/Reels/重輸出共享，角度沒變不重烤
  - `assemble.py`：`_maybe_leveled`/`build_leveled_cmd`/`_leveled_proxy_valid`/`write_leveled_meta` + prepare 用 `render_cfg`（baked 鏡頭 rotate→0）+ plan 帶 `prebake`；`assemble_job` 主合成前先跑/略過預烤（共用 `_pump_progress`）
  - 驗證：unit + 真跑 ffmpeg SSIM smoke = **0.9962**（proxy 路徑與 inline rotate 畫面等價）
  - ⚠ 預烤是「整支 cam B 全長轉正」（~50 分一次性），**首次輸出反而較慢**；回本在第 2 次起（YT+Reels/重輸出）
  - ~~**P2c-follow. 分段平行預烤**：把那一次性 ~50 分用 `-ss` 切塊平行 rotate + `-c copy` concat → ~1.9× 砍到 ~28 分~~
    **❌ 2026-08-07 裁決不做**（理由見下方「待決事項」段）

## 啟動 App（雙擊開介面）

- 打包＝py2app（`setup_app.py`）＋ ad-hoc 簽章（`build_app.sh:100,108`）；雙擊執行的是
  `podcast_toolkit/launcher.py` → `edit.run_dashboard()`，不再經過任何 shell script。
- 安裝＝`./build_app.sh` 產 DMG → 開啟 → 手動拖進 Applications（`build_app.sh` 本身**不會**自動安裝）
  → 首次右鍵開啟。`/Applications/Podcast.app` 目前已裝 v0.2.0（2026-08-05 複驗：4.0G、adhoc、無 quarantine）。
- [x] **自訂圖示** ✅ 2026-07-17（commit `fdf9b15`，銀麥 3D ＋深藍 squircle 底）：素材是
  `assets/AppIcon.icns`（1024×1024，802KB），由 `setup_app.py:51` 的 `iconfile` 掛上，py2app
  打包時自動寫進 `CFBundleIconFile`，不必手動塞檔也不用重簽。本條原本寫的「套到
  `Contents/Resources/applet.icns` + 重簽 + touch」是 AppleScript applet 時代的做法，
  改用 py2app 後已不適用。（2026-08-05 複驗：`dist/Podcast.app/Contents/Resources/AppIcon.icns`
  與 repo 版 SHA256 一致，用的確實是自訂圖示。）
- [ ] **釘 Dock**：之後把 app 拖進 Dock 固定一鍵開。
- app 是**自包含**的（Python runtime、程式碼、ffmpeg、Breeze 全複製進 bundle，共 4.0G），
  **搬 repo 資料夾不會壞**。本條原本寫「app 把 repo 路徑烤死」是 AppleScript launcher 時代的事，
  已不適用（2026-08-05 實測：bundle 內 grep 專案絕對路徑零命中；`config.py:21-26` 凍結態改走
  `RESOURCEPATH` 解析根目錄）。
- [x] **拿掉 `install.sh` 的 osacompile app 生成段** ✅ 2026-08-05：原本那段走的是舊的
  AppleScript 路線，而且會**先把 py2app 版的 `/Applications/Podcast.app` 移進垃圾桶**、
  換成一個指回 repo 的 launcher —— 等於把自包含的 4G app 換成搬家就壞的捷徑。已刪掉整段
  （原 `:137-165`），結尾提示改成「開介面打 `podcast ui`；想雙擊啟動請跑 `./build_app.sh`
  產 DMG 拖進 Applications」。**前 136 行原封不動保留** —— macOS／Python 3.9+／Homebrew 檢查、
  `ffmpeg`、Python 套件（pyyaml ＋ fastapi 等 9 個）、`podcast` CLI symlink，以及整套 Breeze
  後端（clone repo ＋建 venv ＋裝打過補丁的 whisper ＋抓 jieba 繁中詞典），那是轉字幕的地基。
  所以 **`./install.sh` 現在可以安心跑**：它只做環境安裝，不再碰 `/Applications`。

## 2026-07-28 後續（雙行字幕 + 六項 UX 落地後）

- [x] **重打包 .app / DMG** ✅ 2026-07-29：合回 main（`da5b586`）後跑 `./build_app.sh`，
  產出 `dist/Podcast-Toolkit-0.2.0-20260729-da5b586.dmg`，並安裝到 `/Applications/`
  （舊 0.1.0 已移到垃圾桶）。實跑驗證 `/api/version` = `0.2.0+gda5b586.20260729T1320`。
  - ⚠ **裝好還是可能看到舊 UI**：`launcher` 偵測到「已有 podcast server 在跑」就直接開既有
    instance，不會啟新的。本次就撞到一個從 2026-07-16 起、跑了 12 天的舊 build（來自
    tashkent worktree 的 `dist/`，port 55754）—— 裝新版後 `open -a` 只是把它叫到前景。
    之後重打包完若 UI 沒變，先 `pgrep -fl Podcast` 看有沒有殘留舊行程，砍掉再開。
- [x] **`_v2.srt` 缺席時的 fallback 對不回 speakers sidecar** ✅ 2026-07-29：改成
  `_v2.srt` 不在就整集關掉雙行（退回單行），不拿別份字幕的 idx 去配 sidecar。
  `assemble.py:1285`＋回歸測試 `test_prepare_assembly_without_v2_srt_falls_back_to_single_row`。
- [x] **`mic_srt_existing` 實務上恆為空** ✅ 2026-07-29：原判斷（「per-mic 轉錄已否決所以檔不會產生」）
  是錯的 —— 寫檔端還活著（`gemini_subtitle.py:384`，走 `/api/transcribe/per-mic`）。真正的
  bug 在前端：`loadEpisodeState()` 逐欄轉 camelCase 時漏接這欄，三處讀的
  `state.mic_srt_existing` 恆為 `undefined`。已補接線並改讀 `state.micSrtExisting`。
- [x] **Phase 2（單軌集手動配對 UI）** ✅ 2026-08-05 調查後決定**不做**。題目本身就問錯了 ——
  卡點不是「單軌集缺講者標」，是「第二個人的話從來沒進到文字裡」。現行 pipeline 是混音 ASR
  產出唯一一份逐字稿 ＋ 三軌能量逐字 argmax 貼講者（`mic_diarize.py:297`），重疊資訊在
  argmax 那一步就被收斂成單一講者，之後任何卡片路徑都還原不了。實測（20260522 魁哥集，
  四種獨立方法交叉印證）：
  - 字幕卡時間重疊 **0 筆**（1377 張卡），但三軌能量顯示的真重疊講話有 **365 段／161 秒**
    —— 同時講話其實很常見，是資訊被壓掉，不是本來就沒有
  - 那 365 段裡 **96.8% 撐不到 1 秒**（2 秒以上 0 段）
  - **80.5%（294 段）整段判給同一人** → 第二份文字根本不存在
  - 文字判為「實質內容」的 190 段裡，仍有 146 段（76.8%）argmax 從未切換
  - 切 15 段音檔用 Breeze 對**非贏家軌**獨立實聽：5 段「實質內容」樣本聽出獨立第二人發言
    **0 段**；聽得清的都是串音回音（#112 非贏家軌「還要幫 pana 順口講」＝贏家軌本人
    「幫 partner 順口條」）
  值得做的樂觀上限只有 58 段 switch／約 15 秒，而那些內容早已完整躺在單一逐字稿裡，只差
  沒依講者換行。根因是麥克風隔離度僅 10.9–19.3dB（錄音現場的物理限制，軟體補不了），
  這也是當初否決「三軌各自 ASR」的同一個原因 —— 同一套偵測，排除串音 365 段、不排除 2126 段。
  若哪天要處理那 58 段，成本最低的做法是「同一張卡內標示講者變更」，不要碰渲染層。
  （腳本與原始數據當時放 `/private/tmp/multitrack-probe/`，暫存區會被清，**數字以本條為準**。）

## 2026-08-05 後續（使用者手冊 + 移除手動斷句入口後）

- [x] **使用者手冊** ✅ `docs/user-manual/index.html`（40 頁，25 張 UI 截圖）
  ——從開 app 到匯出的全流程，字幕功能寫得最細（點擊位置／達成效果／功能限制）。
  PDF 用 Chrome headless `Page.printToPDF` 印出，**不入 git**（見 .gitignore），
  交付檔另存 `~/Downloads/podcast-toolkit- 教學手冊 20260731b.pdf`。
- [x] **移除檔案清單的「斷句」按鈕** ✅ `app.js` 拿掉 `.srt` 分支 +
  `requestResegment`／`runResegment`（-58 行）。**後端刻意保留**：
  `/api/resegment`（`routes/transcribe.py:164`）與 CLI `podcast resegment`（`cli.py:158`）都還在，
  只是介面上不再暴露 —— 這個入口會覆蓋 `_v2.srt`，使用者手改的字幕會整批消失，風險 > 效益。
- [x] **`merge_short` 預設打開、`merge_target` 放寬到 12** ✅ `defaults.yaml`
  ——四集實測：異味率 10.8%→6.8%（魁哥）、過短卡 -21%～-43%；放寬到 12 再多救 66 張、
  異味率再降 1～2.6pp，風險曲線到 12 為止完全持平。
- [x] **手冊截圖 `17-drawer-files.png` 已過時** ✅ 用 `_scratch/manual_shots_fix4.py`
  重拍（拍攝前先斷言抽屜裡沒有任何「斷句」按鈕，載到舊版前端就整輪失敗），
  再照既有慣例 `sips -Z 2400` 縮成 2400x501。新圖字幕列右側是「— —」。
  其餘 24 張經檔名比對與本次改動無關。
- [x] **重打包 .app** ✅ 2026-08-05 15:05（commit `5eea9b9` 之後）：跑 `./build_app.sh`，
  約 2 分鐘出 app 本體、再約 6 分鐘出 DMG（`dist/Podcast-Toolkit-0.2.0-20260805-5eea9b9.dmg`）。
  驗收沒有只看「build 成功」——比對 bundle 內 `app.css`／`app.js` 與開發樹的 sha256，
  三方（bundle 兩份副本 + 開發樹）完全相同；另確認 `app.css` 含 `height: 40vh`、
  `app.js` 已無 `requestResegment`／`runResegment`；`open` 後抓到 PID、`codesign --verify --deep --strict` 通過。
  ⚠ 裝好若 UI 沒變，先 `pgrep -fl Podcast` 砍掉殘留舊行程（同 2026-07-28 段的教訓）。
- [x] **手冊內文交叉引用做成可點** ✅ 新增 36 處「見第 NN 章／附錄 X」
  的 `<a href="#chNN">`（連原有目錄共 47 個），PDF 內驗出 49 個內部跳轉標註。
  列印樣式本來就有 `a { color: inherit }`，外觀零變化；封面「第 01～03 章」
  這類範圍描述刻意留純文字（拆成兩個連結在列印版更難讀）。
- [x] **手冊 PDF 重出流程固定成腳本** ✅ `_scratch/print_manual_pdf.py`
  ——Letter 612x792pt、`printBackground=true`（不開的話 callout 三色會整片消失）、
  送印前先斷言 24 張圖全部載完。交付檔 `~/Downloads/podcast-toolkit- 教學手冊 20260805a.pdf`
  （帶 a 的才是含頁碼與書籤的版本；不帶 a 的初版已丟垃圾桶）。
- [x] **手冊 PDF 補頁碼與多層書籤大綱** ✅ commit `5eea9b9`：原記載認定 Chrome `printToPDF` 辦不到頁碼和書籤這兩件事，
  要換別的排版引擎重出（估 1-2 天，含重校 40 頁）——這個前提是錯的。實測 Chrome 兩件事都做得到：
  書籤靠 CDP 參數 `generateDocumentOutline: true`，頁碼靠 CSS `@page` 的 `@bottom-center`，Blink 會渲染，
  成本降到 20-30 分鐘。⚠ `@page` 裡絕對不能加 `margin`，加了整份會從 40 頁重排成 42 頁、所有頁碼和書籤位移；
  CDP 那邊既有的 margin 0.4 保留不動。
- [x] **Breeze 轉錄失敗會靜默回報成功** ✅ `transcribe_job.py:658-677`
  ——校對與斷句重整的兩個 `except` 改成寫 `note` 欄（`job:82`），前端把 note 顯示成
  黃底提示（`app.js` 的 `finishTranscribe` + `app.css` 的 `.modal-hint.warn`）。
  失敗仍不擋流程（字幕已匯入），但使用者看得到「跳過了什麼、為什麼」。
- [x] ~~**`sentence_resegment.py` 整支是死代碼**~~ ❌ **誤判，不要刪**：
  `seg_check.py:24` 有 `from podcast_toolkit.sentence_resegment import _is_punct`，
  兩邊刻意共用同一把字數尺；另有 `tests/test_sentence_resegment.py` 在測。
- [x] **`resegment.py` 缺講者重建** ✅ 新增 `remap_speakers_by_time()`（`resegment.py:25`）：
  重切後 idx 全部重編，舊 sidecar 靠「時間重疊最多」重新貼到新卡上，
  覆寫前先備份 `.pre-resegment.bak`；沒有舊 sidecar 就完全不動（不無中生有）。
- [x] **CLI 加 `--src` 選項** ✅ commit `6cd43f4`：`podcast_toolkit/cli.py` 加 `--src PATH`，
  省略時行為零改變；來源檔不存在回 rc=3、不 fallback；輸出一律仍寫回 `_final_v2.srt`；
  講者 sidecar 重建的時間軸基準仍固定取舊 `_v2`。新增 8 支測試。

## 待裁決／待授權

- [x] **決定：`_scratch/print_manual_pdf.py` 移進 repo 納管** ✅ 2026-08-05（commit `d52e0dd`）：
  已搬到 `docs/user-manual/print_pdf.py`（不是原本設想的 `print.py`）。搬之前先修路徑解析 ——
  原本用 `dirname(dirname(__file__))` 往上兩層算 repo root，換位置就會算成 `docs/`；改成同目錄 `HERE`。
  重跑產出 17,831,752 bytes，與搬移前 **byte-for-byte 一致**，證明路徑改對。
- [x] **決定：P2c-follow（分段平行預烤）不做** ❌ 2026-08-07 使用者裁決：
  收益只有首次輸出從約 50 分降到約 28 分（省 22 分鐘、且只有第一次），但要動 5 個高風險點：
  `build_leveled_cmd` 寫死單一 `-i` 且無 `-force_key_frames`；`-c copy` concat 沒有 keyframe 對齊保證；
  meta 無分段清單無法續跑；`_pump_progress` 綁單一 Popen 與單一 total_dur；`_ACTIVE_PROC` 單一全域，
  平行後取消會漏行程；前端只認 `"yt"`/`"reels"`。且**沒有任何真跑 ffmpeg 的測試**
  （`tests/conftest.py:90` 把 `shutil.which` mock 成 True，只驗指令字串），改壞了測試不會紅。
  加上上方 `:102` 的量測：rotate 本身難平行（4 並行聚合僅 1.12×，全核榨頂約 1.9×），收益封頂。
- [ ] **確認：Phase 2（單軌集手動配對 UI，詳見上方「2026-07-28 後續」段）狀態**：原項仍在，
  維持「等使用者授權才開工」，未授權前不動。
