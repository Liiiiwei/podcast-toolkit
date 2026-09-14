# 合成／編輯器 順延項目（第一梯收尾備查）

2026-09-14。來源：seek 快速刪段字卡修復梯次（Batch-1）收尾時盤點的順延清單。
本檔**不含已授權執行項**（那批已完成，見文末附錄 A）；這裡是**下一梯的候選 backlog**，
逐項給到「開工前該知道的根因＋建議做法＋影響檔案」，授權後可直接接手。

排序原則：先「會產生錯誤輸出／資料風險」的，再「UI 未接線」，最後「工程債／體驗」。

| # | 項目 | 類型 | 風險 |
|---|---|---|---|
| D1 | Gemini 時間戳 mod60 反卷積錯誤 | bug（錯輸出） | 中：長音檔字幕時間軸可能整段偏移 |
| D2 | 字幕樣式參數 UI 未接線 | 半成品（設定寫得進讀不回感） | 中：使用者調了沒效果 |
| D3 | 非 atomic 的 SRT／speakers 寫入 | 資料風險 | 中：中斷寫入會留半截檔 |
| D4 | polish 門檻常數散落 | 工程債 | 低：改一個要改多處、易漂移 |
| D5 | 錯字詞典 modal | 缺功能 | 低：目前靠 CLI 產候選、無 UI curate |
| D6 | app.js 拆 render.js | 架構債 | 低（但放大所有返工） |
| D7 | CI python-version matrix | 測試基建 | 中：py3.14 已現兩處環境性紅（見附錄 B） |
| D8 | 近無損母帶編碼 profile | 缺功能 | 低：逐集可選、不影響上傳版 |

---

## D1：Gemini 時間戳 mod60 反卷積錯誤

### 現況
`gemini_subtitle.py` 轉錄長音檔時，模型輸出的時間戳在分鐘進位處有反卷積（deconvolution）
錯誤——秒數 mod 60 還原成絕對時間時，跨分鐘的 cue 會算錯，導致該段之後字幕時間軸偏移。
`post_clean_srt`（`gemini_subtitle.py:147`）目前只處理「幻覺尾段 cue 過濾」與「去標點」，
**沒有**修時間戳；解析走 `srt_io.parse`（`srt_io.py`，`_fmt`/`seconds_to_srt_ts` 在 `srt_io.py:74`）。

> 位置待精確定位：症狀在轉錄輸出的時間戳，需先造一段跨多個整分鐘的音檔重現，
> 再判定是 prompt 輸出格式問題還是解析端的 mod60 還原問題。**不要**憑猜改，先重現。

### 建議做法
1. 造重現案例：≥3 分鐘音檔，確認 01:59→02:00 附近 cue 的絕對秒數是否連續。
2. 定位是「模型輸出就錯」還是「解析還原錯」——前者靠 post 修正、後者改解析。
3. 修正後補一條回歸測試（跨分鐘邊界的 srt 解析斷言）。

### 影響檔案
`gemini_subtitle.py`（`post_clean_srt` 或新增修正層）、`srt_io.py`（若是解析端）、`tests/`（新增回歸）。

---

## D2：字幕樣式參數 UI 未接線

### 現況
後端 `build_style_string(cfg["subtitle_style"])`（`assemble.py:594`）已能吃 `subtitle_style`
組出 ffmpeg `force_style`（`assemble.py:558`），但**編輯器前端沒有把這些參數接出來**給使用者調
（字級、顏色、邊框、MarginV 等）。屬「設定路徑通、UI 入口缺」。

### 開工前必查（本專案 #1 返工根因）
`subtitle_style` 是 episode.yaml 可寫鍵——**先確認它有沒有走完 config.merge 白名單**
（記憶：`config-merge-key-whitelist`，新欄位要動 4 處，否則「寫得進讀不回」）。
接 UI 前先跑 `tests/test_config_roundtrip.py` 確認 round-trip 綠。

### 建議做法
前端加樣式面板（處理 loading/error/empty/success 四態）→ 存回 episode.yaml →
出片時 `build_style_string` 已自動生效。**注意**：卡片文字走 override tag 由 title_cards 畫，
`force_style` 蓋不到它（`assemble.py:283-291` 已註明），樣式面板別誤導使用者以為能改卡片字。

### 影響檔案
`web/static/`（前端面板＋app.js 存取）、`config.py`（若白名單缺鍵）、`assemble.py`（唯讀，已支援）。

---

## D3：非 atomic 的 SRT／speakers 寫入

### 現況
`fsutil.atomic_write_text`（`fsutil.py:14`）已存在，`ingest_breeze.py:118` 已改用。
但仍有寫入點是**非 atomic** 的裸 `write_text`：
- `mic_diarize.py:963` `srt_path.write_text(...)`（診斷輸出的 SRT）
- speakers.json 寫出（`mic_diarize.py:1093` 附近的 write 步驟）需逐一確認是否 atomic。

中斷（當機／被 kill）會留半截檔，下次讀到壞 SRT/JSON。

### 建議做法
全 repo 搜 `.write_text(` 與 `json.dump(` 的 SRT/speakers 寫入點，逐一換 `atomic_write_text`
（json 先 `json.dumps` 成字串再 atomic 寫）。改完各點跑 round-trip：寫入→重載→讀回原值。

### 影響檔案
`mic_diarize.py`、其餘搜出的寫入點；`fsutil.py`（若要加 `atomic_write_json` 輔助）。

---

## D4：polish 門檻常數收斂

### 現況
`subtitle_polish.py` 的門檻常數散落：`MIN_FLASH_SEC = 0.30`（:19）、`SHORT_REVIEW_SEC = 0.80`（:20），
且 `thresholds` dict（:246）另有一份。同一語意的門檻存在多處，改一個要記得改全部，易漂移。

### 建議做法
收斂成單一 source of truth（module 頂的常數群，dict 引用常數而非重寫字面值）。
純內聚重構，行為不變——驗收：改前後對同一批字幕跑 polish，輸出逐字相同。

### 影響檔案
`subtitle_polish.py`（單檔）。

---

## D5：錯字詞典 modal

### 現況
校對候選目前靠 CLI：`glossary_candidates.generate`（`cli.py:62`）產「餵過卻消失的專名」清單
給人工 curate 進詞庫（`cli.py:59` 註解）。**編輯器沒有 modal** 讓使用者直接在 UI curate。

### 建議做法
編輯器加詞典 modal（列候選→勾選加入 glossary→存回 episode.yaml）。
與既有快捷鍵 modal 同層（參考 index.html 既有 modal 結構）。四態齊全。

### 影響檔案
`web/static/index.html`＋app.js、`config.py`（glossary 已是既有鍵，確認 round-trip）。

---

## D6：app.js 拆 render.js（架構前置）

### 現況
CLAUDE.md 架構觸發條件明文：「下次要動編輯器大功能：先拆 app.js（先抽 api.js → render.js；
動刀前先補 Playwright 煙霧測試），再做功能。」全史回顧 app.js ×108 次改動是返工放大器。

### 建議做法
**這是 D2/D5 任何編輯器功能的前置**——動它們之前先做 D6：補 Playwright 煙霧測試 →
抽 render.js → 綠 → 才加功能。不要在未拆的 app.js 上疊新功能。

### 影響檔案
`web/static/app.js` → 新增 `render.js`；`tests/`（Playwright 煙霧）。

---

## D7：CI python-version matrix

### 現況
專案 ruff target py39、使用者機器與交付基準跑 py39，但本機 `python3` 已是 3.14。
本梯測試在 3.14 現出**兩處環境性紅**（見附錄 B）——py39 綠、3.14 紅，正是 matrix 該攔的。

### 建議做法
CI 加 python-version matrix（至少 3.9 + 3.14），讓版本相依的行為差異在 CI 就現形，
不必等本機手動踩到。

### 影響檔案
`.github/workflows/`（或現有 CI 設定）。

---

## D8：近無損母帶編碼 profile

### 現況
記憶 `archival-encode-profile-todo`：之後加近無損母帶 profile（ProRes / CRF14），
逐集可選、不影響上傳版。

### 建議做法
encode 設定加 `archival` profile 選項；出片時可另存一份高位元率母帶。與現行 YT 上傳版並存。

### 影響檔案
`assemble.py`（encode profile）、`config.py`（新鍵，走白名單＋round-trip）。

---

## 附錄 A：本梯（Batch-1）已完成項（已驗收，備查）

1. **HIGH bug — seek 快速刪段路徑靜默吃掉全部標題卡**：`assemble.py` seek 分支改為把
   標題卡用與字幕／講者上色**同一套** `map_src_to_output_time` 重映射到緊密軸（`cards_overlay=compact_cards`）。
   - 回歸測試：`tests/test_dual_line.py::test_prepare_assembly_title_card_survives_seek_cut_inputs`
     （突變驗證過：移除 `cards_overlay` → 測試轉紅 `assert []`）。
   - 抽幀目視：真 ffmpeg 出片，輸出 24s 影格燒進「字卡測試 CARD-OK」黑底卡＋字幕，對照影格（封面 2s）無卡。
2. **刪死碼** `FORCE_ATTACH_PREFIXES`（`subtitle_polish.py`，grep 零殘留）。
3. **⌘＋滾輪縮放** 列加進快捷鍵 modal（`index.html`，與 inline 提示一致）。
4. **jieba importorskip 守衛**：test_word_break(4)＋test_seg_check(5)＋test_subtitle_cleanup(6)
   ＝15 條；收尾另補 `test_mic_diarize.py::test_rap_block_jieba_*`（2 條，原列舉漏掉，同檔 :535 已有先例）。

## 附錄 B：驗收時量到的新問題（留給下一梯）

- **py3.14 環境性紅（2 條，非本梯造成，pristine HEAD 同樣紅）**：
  `tests/test_dashboard_fault_tolerance.py` 的兩條權限容錯測試在本機 3.14 下 warnings 為空
  （chmod 000 在 bash 實測會擋，但 pytest 進程內 `list_episodes` 對 000 的 `recent` 資料夾
  未產生 warning）——py39 基準綠、3.14 紅。**這正是 D7（CI matrix）要攔的**；
  下一梯若動 dashboard，順帶查 `dashboard.list_episodes` 在 3.14 的權限錯誤→warning 路徑。
