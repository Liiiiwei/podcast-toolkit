# 統一時間軸核心：podcast 與影片模式共用一套（標題卡當可選軌）

2026-09-23。來源：使用者要把影片模式的時間軸剪輯 UX 邏輯同步套用到 podcast 模式，
「差別只在標題卡有沒有顯示」。本檔是這條收斂的**權威梯次拆解**。

這條路本來就在 `2026-08-24-video-mode-timeline-trim.md` 的 Phase 3（抽 `timeline.js` 共用核心）
與 Phase 2（併軌 cut 模型）裡。本梯把 **Phase 3 的「抽共用底層」提前做**，Phase 2 的
「剪除語意併軌」明確**不做**、留到接真功能時。

## 決策（已與使用者敲定 = 選項 3）

| # | 決定 | 理由 |
|---|---|---|
| D1 | 這一梯只抽**共用時間軸底層** + 標題卡做成**可選軌** | 純去重、單一 source of truth、零資料風險，剛好是 Phase 3 |
| D2 | **不碰剪除語意**：podcast 仍按句(idx `deletions`)、影片按時間區間(`cuts`) | 兩者語意互斥，強行合併會變第三套刪段機制（CLAUDE.md 硬禁） |
| D3 | **不碰持久化**：標題卡在 podcast 顯示為原型級（不寫回 episode.yaml） | 持久化＝Phase 2/4 範圍，本梯不動後端契約 |
| D4 | 剪除語意統一 + 標題卡真存檔 → 明確延到 Phase 2（接真功能時一起做） | 它牽後端，單獨做會多出一套機制 |

## 現況落差（兩次 Explore 實測，附證據）

**一半是「同源複製兩份」**——影片原型的這些函式註解自己寫著「改寫自 app.js」：
- 波形繪製 `drawWaveform`(video-edit-prototype.js:242) ≈ `drawTlWaveform`(timeline.js:179)
- 靜音參考帶 `drawSilenceGuides`(v-e-p.js:305) ≈ (timeline.js:249)
- 縮放錨點數學 `setZoom`(v-e-p.js:1939) ≈ `setTlZoom`(timeline.js:301)
- 播放頭 + 自動捲動(v-e-p.js:1847-1866) ≈ (timeline.js:128-146)
- 波形資料 `/api/waveform`（schema 已相同）

**沒有任何 JS 函式是跨檔真共用**（都是複製，非 import）。

**會互打的衝突點（本梯要處理或迴避）：**
1. `state.cards` 兩邊**撞名**：podcast=字幕句(app.js:41)、影片=標題卡(v-e-p.js:35)。→ 本梯正名分離。
2. 座標換算基準不同：podcast 有 t0 視窗只畫有字幕區段(timeline.js:29)、影片 t0=0 畫全片(v-e-p.js:334)。→ 抽共用核心時參數化 t0。
3. 剪除模型互斥：`deletions` Set<idx> vs `cuts` [[start,end]]。→ **本梯不碰**（D2）。
4. DOM 單軌(#card-timeline) vs 多軌堆疊(#vt-tracks)。→ podcast 引入多軌容器。
5. 持久化落差：影片原型丟棄式、podcast 真存檔。→ **本梯不碰**（D3）。

## 梯次拆解（本梯 = A；B 延後）

### 前置 T0：補時間軸煙霧測試 baseline
動編輯器大功能前的硬前提（CLAUDE.md）。用 CDP/Playwright 對**兩模式現況**各存一份基準：
zoom in/out 後幾何、播放頭位置、波形有畫、（影片）標題卡軌車道數。之後每步比對防回歸。

### T1：抽 `timeline-core.js` 共用核心
把上列「同源複製」的五項抽成單一模組，**參數化 t0**（podcast 傳首末卡、影片傳 0）：
座標換算(time↔px)、zoom+錨點、播放頭定位+自動捲動、波形繪製、靜音參考帶。
兩模式改成 import 同一份。**驗收：抽完 T0 的 baseline 數字不變（突變測試：改壞核心要能讓 baseline 紅）。**

### T2：`state.cards` 正名分離
影片側標題卡 `state.cards` → `state.titleCards`（或等價），全檔更新引用，消除撞名。
**驗收：影片原型走查全綠、無殘留舊名（全檔搜尋 0 命中）。**

### T3：podcast 引入多軌 DOM + 標題卡可選軌
podcast 時間軸從單軌 `#card-timeline` 改成多軌堆疊容器（沿用影片 `#vt-tracks` 結構），
標題卡軌是其中一條、以 flag/`hidden` 控制顯不顯示（podcast 預設關、影片預設開）。
**驗收：podcast 模式時間軸四態(空/載入/縮放/捲動)截圖正常，波形+播放頭+字幕軌不破版；標題卡軌關閉時完全不佔位。**

### T4：標題卡軌渲染/事件移植到共用座標系
`renderCardTrack`/`bindCardTimeDrag`/`assignCardLanes` 移到共用核心，內部座標從
`t/duration` 改成參數化 t0。標題卡在 podcast 顯示但**維持原型級不存檔**（D3）。
**驗收：影片模式標題卡軌行為與移植前一致（拖移/拖端點/分車道）；podcast 開啟標題卡軌時能顯示但不寫檔。**

### 三類清單（實作分派依據）
- **可直接共用（低成本抽模組）**：zoom、播放頭+自動捲動、波形繪製、靜音帶、`/api/waveform`、對齊設定契約(已共用)。
- **需改寫**：座標換算統一 t0、端點拖曳 handler 參數化、undo 併軌、標題卡軌換座標系、`state.cards` 正名。
- **需新建**：podcast 多軌 DOM 容器 + 標題卡可選開關。（**本梯不建**：剪除語意橋接、標題卡後端持久化 → Phase 2。）

## 梯次 B（延後，不在本梯）
- 剪除語意統一（`cuts` 與 `deletions` 併軌）＝ 原路線圖 Phase 2。
- 標題卡在 podcast 真存檔（後端契約）。
- scrub playhead / 框選 / 整段平移下放到 podcast（加功能，不衝突，可併 B 或獨立梯）。

## 驗收總則
驗收方式＝**CDP 真事件模擬的斷言 pass/fail**（貼近真人手動操作）。不需截圖、不需錄影，
以斷言結果為證據。具體要求：
- 拖曳一律走**完整序列** `Input.dispatchMouseEvent` pointerdown → 多次 move → up（不是設一次座標），
  否則「拖沒掛上」會被漏掉——時間軸全是拖端點/拖卡/刮播放頭，這點最關鍵。
- 每個可點元素驗 `document.elementFromPoint(中心)` **最上層真的是它**，不是只驗存在（防疊層吃掉點擊）。
- 播放頭 seek 前先斷言 `seekable.length>0`（demo server 要支援 Range，否則 seek 靜默失效）。
- 每個「✓」必須綁**布林斷言**（`ok = 實得==期待`），只印值不斷言一律視為未測。
- **突變測試**：每支走查配一次「把受測那行改回舊行為，確認會紅」，證明真的在測東西。
- 改動者不驗收：完成後派 fresh-context agent 對兩模式時間軸做走查 read-back。
- 每個 T 的 baseline 對比要綠、突變要紅，才算該步完成。
