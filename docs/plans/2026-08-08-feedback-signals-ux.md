# 2026-08-08 回饋訊號包（UX 第二梯）

> 梯次制計畫檔（範本：`2026-08-08-subtitle-editing-ux.md`）。來源：第一梯附錄＋2026-08-08 UX 盤點，
> 使用者核准本梯三項：toast 取代 alert、未存檔徽章恆亮、dashboard 壞資料夾容錯。
> 範圍紀律：glossary 的 prompt() 對話框、Web「✨ 一鍵自動」背景 job 是未來梯次；
> confirm()（app.js:3032、5250、6173、7256、7299、7577、7647）與 prompt()（app.js:3336、3338）本梯不動，僅備查。
>
> 架構判斷：本梯**不觸發** app.js 拆分——repo CLAUDE.md 的觸發條件是「編輯器大功能」，
> 本梯是獨立新小元件＋機械替換呼叫點＋數行基準修正，不屬之。

## 診斷結論

| # | 問題 | 根因 | 修法 |
|---|------|------|------|
| 1 | 29 處 `alert()` 當回饋 UI（app.js 26＋dashboard.js 3），阻斷操作、樣式突兀 | 從無 toast 系統，錯誤回報一路沿用 alert | 新建共用 toast 元件（兩頁共用一套），全數替換 |
| 2 | 未存檔徽章一開集就恆亮「2」 | `unsavedCount()`（app.js:405-419）把 `cropYt/cropReels != null` 各記 1（413-414）；載入流程 2802-2814 只要 yaml 存過裁切框就填成非 null | 仿 trim 的 `_saved*` 基準（2849-2853）：載入時存 crop 基準快照，dirty＝與基準不同 |
| 3 | dashboard 單一壞資料夾讓整份集數清單 500 | `dashboard.py:114/116` 與 `episode_stage`（dashboard.py:78→18-30 的 24-29 延遲讀）例外未接，逸出到 `routes/episodes.py:99-104`（無 try）→ 500 → `dashboard.js:64` throw → `renderLoadError`（24-47）整頁換錯誤 | 逐 child 容錯：壞資料夾跳過＋寫進既有 `warnings` 機制（dashboard.py:105-111，前端 dashboard.js:69-72 已會顯示）——失敗不靜默 |

## 1. Toast 系統（取代 29 處 alert）

### 現況（2026-08-08 偵察，計數已實測）

分類：E＝錯誤回報、B＝操作攔阻／輸入驗證。**無成功提示型 alert。**

| 位置 | 情境 | 類 |
|---|---|---|
| app.js:3082 | 偏移秒數非數字 | B |
| app.js:3107 | 套用偏移失敗 | E |
| app.js:3228 | 寫入字典 HTTP 失敗 | E |
| app.js:4416 | 儲存失敗 | E |
| app.js:5665 | 至少要設定一軌 | B |
| app.js:5853 | 至少要選一軌 | B |
| app.js:6076 | 開啟失敗 | E |
| app.js:6279 | 開啟失敗 | E |
| app.js:6346 | 儲存失敗未開始合成 | E |
| app.js:6480 | 儲存失敗 | E |
| app.js:6505 | 載入詞庫失敗 | E |
| app.js:6761 | 儲存詞庫部分失敗 | E |
| app.js:6768 | 儲存詞庫失敗 | E |
| app.js:6920 | 推鏡頭失敗 | E |
| app.js:6932 | 請先選 cam B 來源 | B |
| app.js:6951 | 自動對齊失敗 | E |
| app.js:6992 | 請先選 cam B 或音檔 | B |
| app.js:7046 | 部分對齊失敗 | E |
| app.js:7069 | 對齊或儲存失敗 | E |
| app.js:7082 | 請先選音檔來源 | B |
| app.js:7101 | 自動對齊失敗 | E |
| app.js:7210 | 同步偏移非數字 | B |
| app.js:7216 | 音檔同步偏移非數字 | B |
| app.js:7239 | 儲存失敗 | E |
| app.js:7262 | 回 dashboard 失敗 | E |
| app.js:7616 | 回 dashboard 失敗 | E |
| dashboard.js:121 | 開啟集失敗 | E |
| dashboard.js:176 | init 失敗 | E |
| dashboard.js:252 | 儲存設定失敗 | E |

### 改法

- 新建**一套**共用 toast（「幾個地方在管」原則：兩頁共用同一實作，不做兩份）：
  獨立 `static/toast.js`＋樣式，`index.html` 與 `dashboard.html` 都掛。
- 介面：`showToast(message, kind)`，kind ∈ error／warn；容器右上或底部堆疊、`aria-live`、
  warn 約 4 秒自動消失、error 停留較久（約 8 秒）且可點擊關閉。
- 色票沿用 `tokens.css:62-67`（`--danger/--warning/--success` 與 `*-soft`）；
  定位／動畫可參考 `#update-banner`（app.css:160-184）、樣式參考 `.modal-hint`（app.css:1758-1773）。
- 替換規則：E 類 → `showToast(msg, 'error')`；B 類 → `showToast(msg, 'warn')`，
  原本 alert 後的中止流程（return／不繼續）**行為不變**——toast 只換提示形式，不改控制流。

## 2. 未存檔徽章恆亮

### 現況

- `unsavedCount()`（app.js:405-419）：413-414 把 `state.cropYt != null`／`state.cropReels != null` 各記 1。
- 載入流程 app.js:2802-2804／2807-2814 把存過的裁切框填進 state → 開集即「未存檔 2」。
- 正確做法專案內已有示範：trim 用 `_savedHeadTrimSec/_savedTailTrimSec`（2849-2853）做載入基準。

### 改法

- 載入尾端存 `_savedCropYt/_savedCropReels`（深比較用 JSON 序列化即可，crop 物件形狀固定）。
- `unsavedCount()` 的 crop 兩項改成「與 `_saved*` 基準不同才記 1」。
- 存檔成功後把 `_saved*` 更新為現值（跟 trim 既有流程一致），徽章歸零。

## 3. dashboard 壞資料夾容錯

### 現況（故障鏈）

某 root 下單一資料夾權限錯／壞 yaml → `dashboard.py:114/116` 或 `episode_stage`（24-29）拋例外
→ 逸出 `list_episodes` → `routes/episodes.py:104` 無防護 → 500 → `dashboard.js:63-64` throw
→ `renderLoadError`（24-47）→ 整份清單消失只剩「載入失敗：HTTP 500」＋重試鈕。
既有 `warnings` 機制（dashboard.py:105-111、前端 dashboard.js:69-72）只蓋到 `root.iterdir()` 那層。

### 改法

- 逐 child 包 try：單一資料夾炸掉 → 跳過該集＋`warnings.append("讀不到 <資料夾名>：<原因>")`。
- `episode_stage` 內 24-29 的延遲讀例外 → 該集標 broken（既有慣例）而非上拋。
- **失敗不靜默**：跳過的資料夾必須出現在 warnings（前端已會顯示），不准無聲消失。
- 補 pytest：tmp root 內 1 個好集＋1 個權限 000 資料夾＋1 個壞 yaml 集 →
  `list_episodes` 回好集、warnings／broken 有對應條目；突變對照：把逐 child try 拿掉，測試要紅。

## 驗收（證據填這裡）

依 repo CLAUDE.md：UI 證據＝瀏覽器實測數字＋突變測試；後端＝pytest 輸出。驗收者不得是實作者。

### 前端（CDP 實測）

| 檢查 | 預期 | 實測 |
|---|---|---|
| 開含已存 crop 的集，徽章 | 不顯示（0） | ✅ 假集（episode.yaml 含 crop_yt/crop_reels）開啟後 badge.hidden class=true、count=0（CDP 實測） |
| 改一張卡文字後徽章 | ≥1 | ✅ 改第一卡文字＋dispatch blur → 徽章亮、count=1、status「已修 1」（headless 需手動 dispatchEvent(FocusEvent('blur'))，headless 視窗無焦點、el.blur() 不觸發） |
| 存檔成功後徽章 | 歸 0 | ✅（修正後重測）首輪 ❌：存檔成功但畫面徽章殘留 1 >10 秒——#save-btn 成功路徑（app.js:4431-4432）不呼叫 `renderTopbar()`。實作方修正：`loadEpisodeState()` 尾端補 `renderTopbar()`（app.js:3000-3003）。重測（CDP 假集）：改字徽章 1 → 按存檔 → 250ms 內徽章 hidden、status「已修 0」、srt 已寫入、無 error toast；「合成前先存檔」路徑（:6369）同法實測，按開始合成後 250ms 內徽章歸 0 |
| 突變：註掉 `_savedCropYt` 基準行 | 徽章退回恆亮 2（證明新基準在做事） | ✅ 註掉 app.js:2837-2838 → 重載徽章恆亮 2；還原（md5 與 baseline 一致）→ 重載退回 0 |
| 觸發一個 E 類路徑（mock 儲存失敗） | toast error 出現、非 alert | ✅ mock fetch reject /api/save → `.toast-error` role=alert「儲存失敗：模擬網路故障」；alert 記錄器 0 次呼叫；按鈕回復可按；點擊 toast 可關 |
| 觸發一個 B 類路徑（偏移填非數字） | toast warn 出現、動作有中止 | ✅（改測可達路徑）「偏移填非數字」經 UI 不可達（見附錄 1），改測 cam modal 未選 cam B 按自動對齊（app.js:6956）→ `.toast-warn` role=status「請先選 cam B 來源」、/api/auto-align 請求數 0（動作中止）、無 alert；0.5s 在、4.5s 自動消失 |
| `grep -c "alert(" app.js dashboard.js` | 0 處呼叫（29→0） | ✅ app.js=0、dashboard.js=0；全 static 唯一命中 toast.js:2，為註解非呼叫（已逐筆排除） |

### 後端（pytest）

| 檢查 | 預期 | 實測 |
|---|---|---|
| 新增容錯測試 | 綠 | ✅ `tests/test_dashboard_fault_tolerance.py` 4 passed（chmod 000 權限錯／壞 yaml → 好集保留、壞集 broken 或進 warnings） |
| 突變：拿掉逐 child try | 測試紅 | ✅ 還原 dashboard.py 舊結構（整段無逐 child try）→ 2 failed 2 passed，證明 list_episodes 容錯測試在做事；還原後 md5 與 baseline 一致。⚠️ 但 episode_stage 的突變（try 縮回只包 Episode() 建構）4 測試全綠——「延遲讀例外標 broken」半邊無區辨測試（見附錄 3） |
| 全套測試 | 無新紅（基準 889 passed 1 xfailed） | ✅ 893 passed 1 xfailed（基準 889＋新增 4，零紅） |

## 附錄：驗收時量到的新問題（本梯不修，留給下一梯）

1. **B 類 NaN 攔阻分支經 UI 不可達，且 badInput 會靜默清偏移**：`app.js:3102`（字幕偏移）、`:7231`、`:7237`（cam/audio sync offset）的「填非數字」warn toast 掛在 `type="number"` 欄位上——Chrome 對 number input 的非數字輸入（如 `1e`、`abc`）一律把 `.value` 淨化成空字串，`Number("") === 0`，NaN 分支永遠走不到（死碼）。更糟的是：badInput 狀態下套用會把既有偏移**靜默清成 0**（讀回空字串→0），沒有任何警示。下一梯建議改讀 `el.validity.badInput` 判斷並攔阻。
2. **存檔成功路徑不刷新 topbar**（本次驗收表第 3 項不通過的根因）：`app.js:4431-4432` 存檔成功後呼叫 `loadEpisodeState()`＋`renderCards()`，兩者皆不含 `renderTopbar()`——徽章與「已刪/已修」status 停留在存檔前的畫面值，直到下一個觸發 renderTopbar 的操作或重載頁面。註：此症狀在本梯之前被「crop 恆亮 2」蓋住看不到（徽章反正永遠亮），基準修好後才裸露出來。修法一行：成功路徑補 `renderTopbar()`。**✔ 已修正並重測通過（2026-08-08）**：`loadEpisodeState()` 尾端補 `renderTopbar()`（app.js:3000-3003），主存檔與合成前存檔兩路徑皆實測 250ms 內歸 0（見上方驗收表第 3 列）。
3. **episode_stage「延遲讀例外」半邊無區辨測試**：`dashboard.py:25-35` 整段包 try 的寫法正確，但現有 4 條容錯測試的例外全部發生在 `Episode()` 建構期——把 try 縮回只包建構（突變）測試仍全綠。「.exists() / 延遲讀 cfg 拋錯也要標 broken」這半邊目前沒有測試防守，未來重構可能無感退化。
