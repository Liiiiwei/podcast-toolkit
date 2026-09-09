# 影片模式：時間軸線性剪輯（原型先行）

2026-08-24。來源：使用者要在 app 內新增「影片模式」——像剪輯軟體那樣在時間軸上切分段落、縮短影片。
本檔是**整條路線圖**，但第一梯只執行 **Phase 1（可點原型）**；Phase 2 之後要等原型確認需求才動工。

## 需求（已與使用者敲定）

| # | 決定 | 影響 |
|---|---|---|
| S1 | **不做 YouTube 下載**，影片跟 podcast 一樣由使用者提供本機檔 | 「影片模式」≠ YouTube 功能，是「影片優先 + 時間軸剪輯」的編輯範式 |
| S2 | **只剪掉、不換順序**（order-preserving cut-only） | 直接用現有時間版 `cuts` 模型即可，**不必**動 `build_segment_plan` 做段落重排，省掉最貴的一塊 |
| S3 | 影片單軌、字幕重轉（沿用 Breeze） | 音訊/轉錄流程不變，影片模式只是換一種「剪」的前端 |
| S4 | **先做可點 UI 原型確認需求，不一定要真功能** | Phase 1 是獨立、不落地的 mockup，是全案的檢查點 |
| S5 | 字幕樣式（更多樣式）等使用者整理截圖再談 | 獨立支線，不卡本線；見文末附錄 |

## 核心判斷：兩個模式 = 一套資料層 + 兩個 UI 外殼

這**不是**兩套編輯器互打。podcast 模式（字幕卡刪段）和影片模式（時間軸切段）底層都改**同一份 keep-segment 時間區間模型**（`cuts`）。安全的前提是：動真功能前先把現在半併軌的 cut 模型收斂成一份（Phase 2）。

現況（Explore 實測）：剪輯/刪段有 **4 套機制**（字幕卡刪段 `deletions`/時間版 `cuts`、去空拍 `silence_trim`、`head_trim`、`tail_trim`），只做到「半併軌」——`cut_intervals_from_cfg()`（`assemble.py:164`）彙整了刪段家族＋去空拍，但 head/tail trim 沒進去，單機/雙機還走兩條路（單機用補集餵 ffmpeg、雙機才有明確 keep 清單 `build_segment_plan` `segment_plan.py:38`）。前端目前送 `deletions`(idx) 而非 `cuts`(時間)。專案 CLAUDE.md 硬規則：「≥2 先併軌，禁止疊第三套」——所以 Phase 2 躲不掉，但範圍可縮到「影片模式（單機）實際會碰的路徑」。

---

## Phase 1：可點原型（本梯唯一要做的，其餘等確認）

**目的**：用最低成本敲定「時間軸線性剪輯」的互動模型與版面，讓使用者本人點過確認需求。**不落地、不出片、不碰 `app.js`。**

### 做法
- 新增獨立原型頁 `web/static/video-edit-prototype.html` + `video-edit-prototype.js`。
- **複用現有資產**：`tokens.css`（配色/元件語彙）、`/api/video`（真影片預覽）、`/api/waveform`（真波形＋靜音，`loadWaveform` 沿用）。
- 用一個真集載入，讓原型「看起來/摸起來」像真的，但剪除只是**前端視覺狀態**（不寫回 episode.yaml）。
- 現成可站的基礎：影片預覽（`index.html:215-216` 兩個 `<video>`）、波形 canvas（`drawTlWaveform app.js:1767`）、播放頭同步（`updateTimelinePlayhead app.js:1716`）——原型模仿這套但把「字幕卡塊」換成「自由選區」。

### 原型要展示的互動（走查腳本）
1. 載入影片 → 看到影片預覽 + 波形時間軸 + 播放頭三者同步。
2. 在時間軸上框選一段 → 標記為「剪除」。
3. 視覺呈現：保留段 vs 剪除段一眼可分；即時顯示「剪後總長 = 原長 − Σ剪除」。
4. 多段剪除、取消某段剪除、undo。

### 原型要當場敲定的需求問題（原型存在的理由）
- Q1 剪除手勢：框選一段刪／設進出點刪／播放頭切一刀再刪段？
- Q2 剪除段視覺：變暗／摺疊／紅框？保留段要不要 ripple 自動接合預覽？
- Q3 字幕在影片模式怎麼呈現：要不要同時顯示字幕軌？剪一段時字幕跟著消失並位移？
- Q4 要不要靠齊靜音邊界？（`state.waveform.silences` 現成；但 podcast 模式剛移除吸附，影片模式要不要重來、用哪種形式）
- Q5 剪後預覽：即時看接合結果，還是只標記等出片？
- Q6 undo/redo、鍵盤微調沿用現有慣例（trim 的 ←/→ 0.1s、Shift 0.5s、⌘ 1.0s）？

### 驗收
- CDP headless 截圖走過四態：空（未選）／選取中／已標剪除／多段剪除。
- **使用者本人點過並確認需求**（這是 Phase 1 的真正驗收，不是截圖）。
- 產出「原型確認清單」：把 Q1–Q6 的決定寫下來，當 Phase 4 的規格。

### ⚠️ 防呆：原型是規格，不是實作
原型碼是**丟棄式**的。Phase 4 接真功能時用**抽出的 `timeline.js`** 重建，**不得**把原型的 ad-hoc 碼直接升級成正式版——否則就多出第二套時間軸機制，正是要避免的坑。

---

## Phase 2：併軌 cut 模型（動真功能的硬前提）

範圍縮到影片模式（單機）會碰的路徑：
- 讓單機路徑也走統一 keep-segment（收斂單機 `assemble.py:1357-1360` 與雙機 `build_segment_plan segment_plan.py:38` 的分歧）。
- head/tail trim 收進 `cut_intervals_from_cfg` 家族，不再兩條路各自硬接。
- 前端從送 `deletions`(idx，`episode_io.py:711-723`) 改成送 `cuts`(時間，`episode_io.py:454-469`)。這一步同時完成記憶檔 `deletions-should-be-time-segments` 早想做的遷移。

**驗收**：round-trip（`cuts` 寫入→重載→讀回原值）＋ `test_config_roundtrip.py` 綠 ＋ 出片實測「剪後時長 = 保留區間總和」（數字證據）。

## Phase 3：拆 app.js（架構約定，動編輯器大功能前必做）

- 先抽 `timeline.js`（`app.js:1610-2045`，只碰 `#card-timeline` + `state.cardTimings/waveform`，邊界最乾淨）＋ `api.js`（fetch 集中處：`loadEpisodeState 2877`、`load 3249`、`postSave/buildSavePayload 4349-4457`）。
- **動刀前先補 Playwright 煙霧測試**（CLAUDE.md 硬約定，`app.js` ×108 次改動是返工放大器）。
- 驗收：抽出後既有編輯器行為不變（回歸＋突變測試）。

## Phase 4：接真功能

- 把原型的時間軸剪輯接到真 `cuts`，寫回 episode.yaml，走 `cut_intervals_from_cfg` 出片。
- 驗收：真集出片，剪後成品時長 = 保留區間總和（數字證據），字幕在剪除區間正確消失並位移。

## Phase 5：模式選擇 + 收尾

- 建集/開集加 `mode: podcast|video`（`templates/episode.yaml` + `defaults.yaml`，經 `/api/episode` 帶到前端 `state.mode app.js:2`；建集 payload `routes/episodes.py:235` 目前只有 `{date,name}`，加一個 mode）。
- 前端 `state.mode` 條件顯示面板：影片模式隱藏多機位/講者軌，podcast 模式維持現狀。已有近似分岔可參考：輸出 target `yt/reels/mp3`、`state.activeVersion`。
- `mode` 是新可寫鍵 → 要過 `config.merge` 白名單 + round-trip。

---

## 施工順序與檢查點

```
Phase 1（原型）──[使用者確認需求]──> Phase 2（併軌）──> Phase 3（拆 app.js）──> Phase 4（接真功能）──> Phase 5（模式+收尾）
```

**Phase 1 後強制停下**：若原型確認需求 → 往下走；若原型翻出不同需求（例如其實想要重排、或想要更接近 CapCut 的操作）→ 回來改本計畫，別硬接。

## 風險

| 風險 | 防法 |
|---|---|
| 原型碼被直接升級成正式版 → 第二套時間軸機制 | Phase 1 的「原型是規格不是實作」條款；正式版用抽出的 timeline.js |
| 沒做 Phase 2 就接真功能 → 踩單機/雙機分歧 | Phase 2 當 gate，Phase 4 不得先行 |
| 不拆就在 8405 行 app.js 上加 → 返工放大 | Phase 3 gate + Playwright 煙霧測試 |
| 「影片模式」與輸出格式「YT」撞名 | 模式命名用 podcast / 影片(video)，不要叫「YT 模式」 |

## 附錄：本梯不做，但已納入路線

- **字幕樣式（功能二，等使用者截圖）**：現況只有一套機制（`build_style_string assemble.py:84-96` 組 force_style，SRT→中繼 .ass 燒錄），10 個參數（字型/字級/顏色/描邊/粗體/陰影/位置…`defaults.yaml:106-114`）都已實作，但編輯器 UI 只開放 `font_size`。最高 CP 值的「新樣式功能」其實是把已有 9 個參數接上 UI（Layer A）；force_style 做不到的（半透明底色塊、逐字高亮）才要改 `_write_ass_from_srt`（Layer B）。獨立於本線，可並行。看到截圖再細化。
- **命名建議**：episode.yaml 用 `mode: podcast|video`；UI 顯示「Podcast 錄音室」vs「影片剪輯」。
