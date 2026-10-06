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

---

## 第二刀（2026-09-25 同日）：renderTrimControls ＋ 字幕樣式面板渲染端

### 切點（同一套量化判準，改以「群組」為單位算）

把互相引用的符號整組搬，「需新增 export」常常能歸零——這是第一刀沒用上的槓桿。

| 候選群組 | 搬走行數 | 需新增 app.js export | 比值 | 結論 |
|----------|---------|----------------------|------|------|
| 時間編輯子系統（`buildTimeToolbar`／`toggleTimeEdit`／循環試聽…） | ~390 | 5 | 78 | 延後，見下方提案 |
| 字幕樣式面板渲染端（4 符號） | 80 | **0** | ∞ | **採用** |
| 　＋ `commitCaptionStyleField`／`toggleCaptionStylePanel` | 125 | 2 | 62.5 | 否決：那兩支是動作不是渲染 |
| `renderTrimControls` | 47 | **0** | ∞ | **採用** |
| `renderCropInfo` ＋ `applyRotationPreview` | 67 | 3 | 22.3 | 比值最差，延後 |

採用 = 116 行、**app.js 新增 export 0 個**，app.js 改從 render.js import 回 4 支。
`assColourToHex` 只被群內的 `renderCaptionStyleControls` 用，跟著搬且不對外 export；
`hexToAssColour` 被留在 app.js 的 `commitCaptionStyleField` 用，所以不動。

### 護欄缺口：這次是「容器」沒取樣到

第一刀漏的是**時點**（loading 態），這一刀開工先查，發現漏的是**容器**——
原本 8 個容器沒有一個蓋到要搬的這兩支。動刀前補到 13 個容器 ＋ S11／S12 兩個狀態，
重錄 baseline 並比對舊檔確認**原 10 狀態 × 8 容器逐字不變**。

另外解掉一個真盲點：`<input>`／`<select>` 的 `value`／`checked` 設定後
**outerHTML 完全不變**，字幕樣式面板九成的輸出都在這些活值上。
補了合成指紋鍵 `capStyleVals`（欄位串成 `id=value|disabled`）才測得到——
MUT-R4 證實：色碼欄寫死時 `capStyleVals` 全紅、`capStyle` 的 HTML 指紋全綠。

### 驗收

- [x] 動刀前重錄 baseline，原 10 狀態 × 8 容器指紋逐字不變
- [x] 護欄 **254/254**（12 狀態 × 15 指紋）；`verify_render_skeleton_loading.py` 2/2
- [x] MUT-R3（trim 提示字串）／MUT-R4（色碼欄寫死）各自只紅對應容器，還原後全綠
- [x] 靜態符號核對：4 支在 app.js 都有 import，`assColourToHex` 用量歸 0
- [x] app.js 新增 export **0 個**
- [x] `/usr/bin/python3 -m pytest -q`：**1053 passed, 1 xfailed**
- [x] app.js 7756 → 7645 行；render.js 613 → 741 行
- [x] MUTATIONS.md 登記 MUT-R3／MUT-R4

### 下一刀的提案（未授權，先寫著）

**時間編輯子系統另開 `timeedit.js`**：`buildTimeToolbar`、`toggleTimeEdit`、
循環試聽與鍵盤微調互相纏繞，搬進 render.js 會讓那個檔變成第二個 app.js；
另開新檔約 390 行、需 app.js 新增 5 個 export
（`_auditionEnd`、`_undoCoalesce`、`clearCardTimings`、`getEffectiveCardTime`、`setCardTime`）。
比值 78，數字上划算，但它是**架構決定**不是搬運，要單獨開梯並先補「卡片編輯」的指紋狀態。
（`renderCards` 不屬這一組——它是整列渲染的中樞，要 29 個 export，見第一刀的表已否決。）
`renderCropInfo` 那一組（67 行／3 export，比值 22.3）等裁切相關功能要改時順手帶走。

---

## 第三刀（2026-09-26）：⏱ 時間編輯工具列子系統 → `timeedit.js`

### 為什麼另開新檔而不併進 render.js

第二刀提案裡算過比值 78，數字划算。但真正的理由不是行數：這一組九成不是渲染，
而是**互動狀態機**——循環試聽的 `timeupdate` listener、document 層的 keydown、
undo 連發合併、工具列開關的控制器物件。塞進 `render.js` 會讓那個檔變成第二個
`app.js`（渲染與動作再度混在一起），等於把第一刀的成果吃回去。

### 搬走的 19 支（403 行）

時間換算與目標抽象：`parseTimeCard`（私有）、`cardTimeTarget`、`newCardTimeTarget`

循環試聽：`_timeEditCtl`、`TIME_LOOP_PAD`、`_teLoopOn`、`_teLoopAttached`、
`onTimeLoopTick`、`startTimeLoop`、`stopTimeLoop`、`toggleTimeLoop`、`syncTimeLoopBtn`

工具列 DOM 與互動：`timeOverlapWarnings`、`buildTimeToolbar`、`mkTimeInput`、
`TIME_NUDGE_STEPS`（含 top-level keydown 掛載）、`onTimeNudgeKey`、
`toggleTimeEdit`、`closeTimeEdit`

### app.js 新增的 export 恰 5 個——但其中 2 支必須是 setter

`getEffectiveCardTime`、`setCardTime`、`clearCardTimings` 直接加 `export`。
另兩支不能照搬：**ESM 的 import 是唯讀繫結**，`timeedit.js` 不可能跨檔對 app.js 的
`let` 賦值（會直接 TypeError）。原本兩處直接賦值改成：

| 原本（同檔內賦值） | 改成 |
|--------------------|------|
| `_undoCoalesce = e.repeat === true;` | `setUndoCoalesce(e.repeat === true);` |
| `_auditionEnd = null;` | `clearAudition();` |

反向同理：app.js 原本三處各寫兩行 `_timeEditCtl = null; stopTimeLoop();`
（刪新卡／換集／插卡），收成 `timeedit.js` 匯出的 `resetTimeEdit()`。
`_teLoopOn`／`_timeEditCtl` 則以 `export let` 給 app.js **只讀**（live binding，合法）。

### 護欄：動刀前先把「卡片編輯」的狀態補上

第一刀漏的是**時點**、第二刀漏的是**容器**，這一刀開工先查，發現漏的是**狀態**——
既有 12 個狀態沒有一個把 ⏱ 工具列開起來過。動刀前擴充到 **17 狀態 × 17 指紋**：
容器加 `timeBar` (`.card-time-edit`)，並比照 `capStyleVals` 的做法補合成鍵 `timeVals`
（`<input>` 的 `value` 是 IDL 屬性，設定後 outerHTML 一字不變，不補就測不到）。
S13–S17 五個新狀態：工具列展開／改過時間／循環試聽開啟／重疊警示／新卡工具列。

寫 S13–S17 的期待值時踩到一次：**編輯器卡片時間是顯示軸（磁碟軸 + `alignShift`）**，
沙盒集 `subtitle_offset_sec: 1.5` 所以工具列顯示值比 srt 多 1.5 秒。第一版期待值
寫成磁碟軸而紅了——那是**期待值的前提錯，不是產品碼錯**，改期待值不算放寬門檻。

### 第三道關卡：`jsc -m` 的 module link

這一刀多用了一道免費的靜態關：`jsc -m file.js` 在**執行前**就會檢查 import 名，
import 了不存在的 export 會拋 `SyntaxError: Importing binding name 'X' is not found.`。
突變證實有效（把 `clearAudition` 改成 `clearAuditionXX` → 立刻紅）。

但它**抓不到反向**——「用了卻沒 import」是 runtime ReferenceError，會被靜默 `return`
吞掉（第一刀的 `renderVersionLabel` 正是這一型）。所以搬動式重構要三道關：
**動態指紋（行為零變更）＋ 靜態符號 grep（用了卻沒 import）＋ jsc module link（import 了卻沒 export）**。

### 驗收

- [x] 動刀前把護欄從 12 狀態 × 15 指紋擴充到 **17 狀態 × 17 指紋**並重錄 baseline
- [x] MUT-R5（工具列時間欄寫死）／MUT-R6（循環試聽不掛 listener）各自只紅對應指紋，還原後全綠
- [x] 抽出後重跑護欄 **392/392**，與 baseline 逐一相同；`verify_render_skeleton_loading.py` 2/2
- [x] 靜態符號核對：19 支逐一 grep 現版 app.js，數字全部對得上（12 支歸 0、7 支只剩 import＋呼叫）
- [x] `jsc -m` 三檔（app.js／render.js／timeedit.js）module link 通過，並以突變證明這道關會紅
- [x] app.js 新增 export **恰 5 個**（與第二刀的預估一致），其中 2 支是 setter
- [x] app.js 7645 → **7256 行**；新建 `timeedit.js` **457 行**，對外 export 9 支
- [x] `tests/conftest.py` 的 `EDITOR_JS` 加上 `timeedit.js`
- [x] `/usr/bin/python3 -m pytest -q`：**1053 passed, 1 xfailed**
- [x] MUTATIONS.md 登記 MUT-R5／MUT-R6 與「軸別 ≠ 產品碼錯」的判準

### 還留在 app.js 的（給下一刀）

`renderCards`（590 行／29 export，比值 20.3）——**維持否決**，它是整列渲染的中樞。
`renderCropInfo` ＋ `applyRotationPreview`（67 行／3 export，比值 22.3）——等裁切
相關功能要改時順手帶走。`renderNewCardRow` 只在最後兩行碰到 timeedit.js，不急。
