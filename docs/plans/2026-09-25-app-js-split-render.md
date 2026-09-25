# D6：把 app.js 的渲染群抽成 render.js（第一刀）

> 依據：專案 CLAUDE.md「架構觸發條件」——「下次要動編輯器大功能：先拆 app.js
> （先抽 api.js → render.js；動刀前先補 Playwright 煙霧測試），再做功能。」
> api.js 已抽（`api.js` 檔頭有邊界說明），本梯做 render.js。
> 狀態：計畫中（2026-09-25 開）

## 為什麼現在做

2026-08-08 全史回顧的結論：`app.js` 被改過 108 次，是返工放大器。現在 8285 行，
`state`、渲染、事件綁定、後端往返殘段全擠在同一檔，任何一次改動都要在整檔裡找上下文。
這一刀的目的**不是讓檔案變短**，是把「畫面怎麼長出來」從「資料怎麼變」裡切出去，
之後動鏡頭／字幕樣式這類功能時，改動半徑能收在 render.js 內。

## 這一刀切哪裡（量化決定，不是憑感覺）

判準：**搬走行數 ÷ 需要在 app.js 新增的 export 數**。分母就是新的耦合面，愈小愈好。

| 候選 | 搬走行數 | 需新增 app.js export | 結論 |
|------|---------|----------------------|------|
| 只搬 `renderCards` | 592 | 29 | 否決。它是編輯器的中樞，搬它等於把 app.js 對半剖開再用 29 個 export 縫回去 |
| 渲染群 D（本梯） | 550 | 3 | **採用** |

### 群組 D 名單（26 支）

尺規與講者：`renderCamRuler`、`renderSpeakerRuler`、`computeEffectiveSpeaker`、
`speakerLabel`、`computeEffectiveCamera`、`buildEffectiveCameraMap`

字幕預覽：`renderCaption`、`captionLineEl`、`applyCaptionStyle`、`applyCaptionLineStyle`、
`captionPreviewStyle`、`computeCaptionScale`、`placeCaptionOverlay`、`showsTwoLineCaption`、
`activeSubtitleStyle`、`renderCaptionSizeControl`、`activeCardAt`、`activeCardsAt`

卡片輔助與工具列：`reviewReasonLabel`、`cardNeedsReview`、`camBtnTitle`、
`renderCardSkeletons`、`renderReviewToolbar`、`renderSusToolbar`、`renderTopbar`、
`renderVersionLabel`

一起搬走的群內專用符號（只被群內用，留在 app.js 就是白白多 4 個 export）：
`CAPTION_BOX_CSS_KEYS`、`CAPTION_LINE_CSS_KEYS`、`_captionCss`、`parseBuildId`。

### app.js 只需新增 3 個 export

`checkedDeletionSeconds`、`unsavedCount`、`getActiveCrop`。
（`state`、`$`、`fmtTime`、`fmtTimeCard`、`expandedCards`、`pushUndo`、`rerenderEditState`
已經是 export，直接用。）

### 明確留給後續刀次

`renderCards`（592 行）、`buildTimeToolbar`、`renderNewCardRow`、`renderTypo`、
`renderCropInfo`、`renderTrimControls`、`renderCaptionStyleControls`。
這一梯不碰，理由見上表：它們對 app.js 內部狀態的依賴面還太寬，要先有 render.js
當落點、再逐步把共用的小工具挪過去，耦合面才會收斂。

## 循環 import 的鐵律（照 timeline.js／api.js 的既有慣例）

`render.js` 會 `import { state, $, ... } from "./app.js"`，app.js 也會 import render.js
——這是既有的循環 import 形狀，可行，但有一條鐵律：

> **本檔模組求值階段（top-level）絕不可呼叫或讀取任何從 app.js 匯入的東西。**

因為 app.js 是進入點，它的 `const`/`let` 在 render.js 求值時還在 TDZ。
只能在函式體內（被呼叫時）讀。檔頭要比照 timeline.js／api.js 寫清楚邊界與這條規則。

## 護欄：動刀前先錄渲染指紋

重構的驗收條件是**行為零變更**，而 diff 證明不了這件事。做法：

1. 寫 `scripts/cdp-walkthrough/timeline-baseline/verify_render_fingerprint.py`，
   逐一造出各渲染狀態，對每個渲染容器抓**正規化後的 outerHTML** 算指紋，落檔成 baseline。
2. 狀態清單（每個都要有布林斷言確認真的進到那個狀態，不能只印值）：
   初始載入／單機集／雙機集（patch `episode.yaml` 加 `cameras.b` 後重啟）／
   待複查卡／⏱ 時間工具列展開／字幕預覽（含兩行）／版本標籤。
3. 正規化要拿掉會漂的東西（build id、時間戳、隨機 id），但**不能順手拿掉會動的真內容**
   ——正規化規則寫進檔頭，並靠突變測試證明它沒把差異洗掉。
4. 動刀後重跑，指紋逐一相同＝零行為變更。

### 突變測試（證明指紋在測東西）

- MUT-R1：故意改一支 render 的輸出字串 → 對應容器指紋必須變、其餘不變。
- MUT-R2：故意讓正規化規則吃掉一段真內容 → 要能看出它把差異洗掉了（反向確認）。
- MUT-GREEN：兩項還原後全綠。

## 驗收清單

- [x] baseline 指紋在**動刀前**錄好並落檔（`verify_render_fingerprint_baseline.json`）
- [x] 每個狀態都有布林斷言確認真的進到該狀態（不是只印 HTML）
- [x] MUT-R1 會紅（改 render 輸出 → 指紋變）
- [x] MUT-R2 會紅（正規化洗掉真內容 → 看得出來）
- [x] 抽出後重跑走查，**所有指紋與 baseline 逐一相同**
- [x] `render.js` 檔頭寫明邊界（哪些該進來、哪些不該）與 TDZ 規則
- [x] `tests/conftest.py` 的 `EDITOR_JS` 加上 `render.js`
- [x] `/usr/bin/python3 -m pytest -q` 全綠（3.9.6；`python3` 是 3.14，有既有恆紅測試）
- [x] app.js 新增的 export 恰為 3 個，沒有為了省事多開
- [x] 群內專用的 4 個符號確實跟著搬走，app.js 內無殘留
- [x] MUTATIONS.md 登記本梯突變

## 實際結果（2026-09-25 收尾）

- `app.js` 8285 → 7756 行（搬走 554 行）；新建 `render.js` 613 行，export 21 支。
- `app.js` 新增 export **恰 3 個**：`getActiveCrop()`、`unsavedCount()`、`checkedDeletionSeconds()`。
- 群內專用的 4 個符號（`CAPTION_BOX_CSS_KEYS`／`CAPTION_LINE_CSS_KEYS`／`_captionCss`／
  `parseBuildId`）在 `app.js` 為 0 次、只在 `render.js` 內部用，未對外 export。
- 護欄 **136/136**，10 個狀態 × 8 個容器指紋與 baseline 逐一相同；全測試
  **1053 passed, 1 xfailed**（`/usr/bin/python3 -m pytest -q`）。

### 護欄抓到的真缺陷

抽完檔第一次跑是 **126/136**：10 個狀態的 topbar 指紋全紅（427 → 384 字元），
版本標籤停在「版本 …」。根因是 `renderVersionLabel` 仍被 `probeVersion()` 呼叫 5 次
卻漏了 export/import，ReferenceError 被該函式每個分支的靜默 `return` 吞掉。
補 export 後回到 136/136。

### 覆蓋缺口（誠實記錄）

`renderCardSkeletons` 同樣漏了 export，但**指紋護欄一條都沒紅**——10 個狀態
全落在「卡片已經回來」之後，沒有一個在 loading 時點。它是靠「拿動刀後的 app.js
當地面真相、逐一 grep 30 個符號」的靜態核對抓到的。

缺口已補成 `scripts/cdp-walkthrough/timeline-baseline/verify_render_skeleton_loading.py`（2/2），
突變（拿掉該 export）確認會紅。結論寫進 MUTATIONS.md：
**搬動式重構要同時有「動態指紋」與「靜態符號核對」兩道，缺一會漏。**

### 沒搬的（留給後續刀次）

`renderCards`、`buildTimeToolbar`、`renderNewCardRow`、`renderTypo`、`renderCropInfo`、
`renderTrimControls`、`renderCaptionStyleControls`——它們對 `app.js` 內部狀態的
依賴面還太寬，先有 `render.js` 這個落點再逐步挪。
