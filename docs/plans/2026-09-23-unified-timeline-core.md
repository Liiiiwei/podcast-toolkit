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

## 梯次 B 執行拆解（2026-09-23 敲定：最小版，使用者選「最小」）

三路 Explore 勘查後校正的地面事實：後端 `cut_intervals_from_cfg`(assemble.py:171) 已把
`deletions`→`cuts` 自動遷移，podcast/video 兩模式其實已共用同一聚合入口；標題卡其實已能經
`/api/apply-plan`→plan_io 存檔(但不在 round-trip 測試覆蓋內)；`mode` 旗標全 repo 不存在，且
`state.mode` 已被轉錄模式佔用(app.js:4958)。故最小版**不動出片管線、不引入第三套剪段機制**。

| 項 | 內容 | 風險 | 派給 | 驗收條件 |
|---|---|---|---|---|
| B4 | `layoutMode: podcast\|video` 新鍵（避開 `state.mode`），控標題卡軌在 podcast 正常 UI 顯不顯 | 中 | BE | round-trip：寫入→重載→讀回；`test_config_roundtrip` 綠 |
| B2 | 標題卡 podcast 真存檔（純文字卡 `id/start/end/text`＋tpl 預設；buildSavePayload→save_state；補 `_WRITER_SOURCES`＋SAMPLES） | 中 | BE＋FE-2 | round-trip；CDP 存檔後重載標題卡還在 |
| B3a | scrub 播放頭下放（`bindPlayheadScrubCore`，與剪除零耦合） | 低 | FE-1 | CDP 完整拖曳序列＋`seekable.length>0`＋突變測試 |
| B3b | 字幕塊整段平移下放（改 timing 非 cuts） | 低 | FE-1 | CDP 完整拖曳＋鄰句夾制斷言＋突變測試 |
| B3c | 框選 marquee 下放，**終點換算成涵蓋卡 idx 併入既有 `deletions`**（不碰 cuts、不動 proofread、零後端改動） | 中 | FE-2 | CDP 框選後對應卡進 deletions＋突變測試 |
| ~~B1~~ | 剪除語意結構性併軌 / 單雙機渲染收斂 | 高 | **最小版不做** | 兩模式已共用 `cut_intervals_from_cfg`，維持現狀；真正單一 source 另開專梯 |

**波次（app.js 禁並行改，×108 返工放大器）：**
- Wave 1（可並發）：BE（純 Python：episode_io/config/templates/defaults/routes/episodes/test_config_roundtrip）‖ FE-1（純 timeline-core.js/timeline.js，不碰 app.js）
- Wave 2：FE-2（app.js＋timeline.js：B2 送存、B4 顯示切換、B3c marquee），依賴 BE 契約＋FE-1 基座
- Wave 3：fresh-context CDP 走查驗收（兩模式），改動者不驗收；每支走查配突變測試

## B1 專梯：剪除語意單一 source of truth（2026-09-24 開工）

### 勘查結論：「刪段這件事現在有幾個地方在管？」

| # | 位置 | 管什麼 | 狀態 |
|---|---|---|---|
| 1 | `episode.yaml: deletions`（卡 idx list） | podcast 編輯器的持久化格式 | **並存中** |
| 2 | `episode.yaml: cuts`（`[[start,end]]` 秒） | 影片模式／`/api/apply-plan` 的持久化格式 | **並存中** |
| 3 | `assemble.cut_intervals_from_cfg`(assemble.py:171) | 唯一聚合入口（cuts 優先、deletions 自動換算、並存印警告） | 已單一 ✅ |
| 4 | `assemble._pad_and_merge_cuts`(assemble.py:107) | 後端合併：連刪跨停頓併、夾保留卡語音邊界、每側 cut_pad | 唯一 ✅ |
| 5 | `app.js mergeDeletionIntervals`(app.js:3181) | 前端預覽跳段的**另一套**合併：連刪跨停頓併、夾下一張保留卡起點、**無 pad** | 與 #4 平行 |
| 6 | `video-edit-prototype.js state.cuts` + 自家 merge(:208) | 影片原型的剪除 state | 獨立 |
| 7 | `episode_io.save_state`(:453-474) | 兩條寫入路徑並列（deletions 翻譯 composite id／cuts 正規化） | **並存中** |
| 8 | `episode_io.load_state`(:261-264) | 兩個鍵都回前端 | **並存中** |
| 9 | `config.merge`(:230-232) | 兩鍵都透傳 | **並存中** |
| 10 | `plan_io.apply_plan`(:197-200) | 只寫 cuts，不碰既有 deletions | **會製造並存** |

**真正的病灶**：不是後端（#3 已單一），是**儲存層兩個鍵並存 + podcast 前端完全忽略收到的 `cuts`**
（`app.js` 對 `data.cuts` 零引用，只讀 `data.deletions`）。後果：影片模式存過 cuts 的集，
在 podcast 編輯器裡**看不到那些刪段、也移不掉**，但出片時它們生效且把整份 `deletions` 踢掉
（只有 stderr 一行警告，使用者在 UI 看不到）。這正是「兩套機制互打」的典型。

### 決策（對齊 cameras 的既有前例）

鏡頭切換點已經做過同一件事：**時間版為真相（`_v2_cameras.json`）、前端維持「卡 key」介面**
（`episode_io.py:286` 的 `transitions_to_card_mapping`）。刪段照抄這個形狀：

- **持久化只留 `cuts`（時間版、`_v2.srt` 磁碟軸）**；`deletions` 降級成唯讀舊格式，
  任何一次存檔就完成遷移（cuts 有值時後端強制移除 `deletions`，不留並存可能）。
- podcast 前端**維持 `state.deletions`（Set<卡 key>）當操作介面**不改 UI 語意，
  只在載入／存檔的邊界做時間↔卡 key 換算（純函式放進 `timeline-core.js`，可單測）。
- 換算不回卡的 cut（影片模式的任意區間、跨非連續卡）→ 進 `state.foreignCuts`
  **原樣 round-trip 保存**（絕不因為卡層 UI 表達不了就擴寬或吞掉），並在 UI **可見**標示。

### 等價性保證（為什麼輸出不會變）

`cut_intervals_from_cfg` 走舊路時做的就是 `_from_idx(deletions)` =
「每張被刪卡的 `(start, end)`」；前端換算寫出的 `cuts` 逐筆等於同一組區間 →
後端拿到的 `intervals` 逐位元相同，之後的 `_pad_and_merge_cuts` 完全一致。
卡 key→cuts **逐卡一段、刻意不預先合併**，連 `cut_pad=0`（後端該分支不合併）都逐位元相同；
要不要把連刪跨停頓併起來，仍由後端 `_pad_and_merge_cuts` 單一決定。
反方向（cuts→卡 key）則要求 cut 的外緣對齊、且涵蓋的卡彼此連續，否則判 foreign
——跨真停頓的整段 cut 若換成卡 key 會讓停頓復活，不等價。

### 執行項

| 項 | 內容 | 驗收條件 |
|---|---|---|
| B1-1 | `timeline-core.js` 加純函式 `cardKeysToCuts` / `cutsToCardSelection` | jsc 實跑單測（含 foreign 保真、flush 合併） |
| B1-2 | `app.js` 載入吃 `data.cuts`（cuts 優先、與後端同序）、存檔送 `cuts` + `deletions: []` | CDP：刪卡→存檔→yaml 只有 cuts；重載紅卡一致 |
| B1-3 | `episode_io.save_state`：cuts 非空 → 強制移除 `deletions` 並印可見訊息 | pytest round-trip |
| B1-4 | `plan_io.apply_plan`：寫 cuts 時一併移除 `deletions` | pytest |
| B1-5 | foreign cuts 在 podcast UI 可見（統計列 + 載入提示），不靜默 | CDP 斷言文字 |
| B1-6 | JS↔Python 差分測試：同一組卡/刪段，前端換算出的 cuts 餵 `cut_intervals_from_cfg` 與舊 deletions 路徑結果相同 | pytest（jsc 實跑） |

**不在本梯**：`app.js mergeDeletionIntervals`（#5）與後端 `_pad_and_merge_cuts`（#4）的合併規則併軌
（預覽跳段目前不吃 `cut_pad`，差 0.15s/側）。它需要把 pad 演算法搬進 JS 並把 `cut_pad` 下放前端，
與本梯的儲存層併軌正交；列為附錄留給下一梯。

### 完成狀態（2026-09-24 收工）

| 項 | 狀態 | 證據 |
|---|---|---|
| B1-1 | ✅ | `tests/test_cuts_frontend_mapping.py` 7 測（jsc 實跑真的 `timeline-core.js`，含 foreign 保真、跨停頓不併卡、round-trip） |
| B1-2 | ✅ | `verify_b1_cuts.py` 測項 2c／3b／3c／4b（真點刪除鈕＋`elementFromPoint` 疊層驗證→存檔→yaml 只剩 cuts→重載紅卡一致） |
| B1-3 | ✅ | `tests/test_episode_io.py` 新增 cuts 非空強制移除 `deletions` 的 round-trip 與可見訊息斷言 |
| B1-4 | ✅ | `tests/test_plan_io.py` 新增 `apply_plan` 寫 cuts 時一併移除 `deletions` |
| B1-5 | ✅ | `verify_b1_cuts.py` 測項 5b（統計列出現「影片模式剪段 1 段（0.4s，本頁不可編輯）」）、5c（不被擴寬）、6d（原樣 round-trip 回 yaml） |
| B1-6 | ✅ | `tests/test_cuts_frontend_mapping.py::test_frontend_cuts_equal_legacy_deletions_path`（`cut_pad` 參數化：前端換算出的 cuts 餵 `cut_intervals_from_cfg` 與舊 deletions 路徑逐位元相同） |

驗收數字：`verify_b1_cuts.py` **22/22 通過**；六個突變（`run_b1_mutations.py`）**全部如預期變紅**、
還原後回歸 22/22（明細見 `scripts/cdp-walkthrough/timeline-baseline/MUTATIONS.md` #7～#12）；
`pytest -q` **1020 passed, 1 xfailed**。

過程中修掉一個原本會靜默出錯的產品碼缺陷：`api.js: _cutOffset()` 缺了 `applyState` 那邊有的
`state.audioPath` 守衛 —— 集有 `audio_sync_offset` 但沒掛外接音檔時，兩邊位移不對稱，
載入時整批 cuts 會偏掉 sync_offset 秒、全部掉進 foreign（畫面上刪段憑空消失、卻照剪）。已補上守衛。

**不在本梯（附錄，留給下一梯）**：

1. `app.js mergeDeletionIntervals`（勘查表 #5）與後端 `_pad_and_merge_cuts`（#4）的合併規則併軌
   —— 預覽跳段不吃 `cut_pad`，差 0.15s/側。
2. `new:` 開頭的新增卡不進 cuts：新卡在磁碟 `_v2.srt` 上還沒有對應時間段，存檔時先寫 srt 再算 cuts
   才有意義；目前新卡被刪不會寫成剪段（影響面極小，但是已知落差）。
3. `api.js: toDiskTime(t)`（:42）仍是「無 `audioPath` 守衛、取 2 位小數」的既有版本。
   本梯只讓 cuts 走新的 `cutToDiskTime`（3 位小數，對齊 SRT 毫秒精度），沒動它的呼叫者 ——
   改它會牽動字幕時間寫回，屬另一件事。

---

## B2 專梯：前端／後端刪段合併規則併軌（2026-09-24 完成，＝上梯附錄第 1 項）

### 勘查結論：「刪段的合併規則現在有幾個地方在管？」

兩個，規則不一致，所以預覽跟成品對不起來：

| # | 位置 | 規則 |
|---|---|---|
| 1 | `assemble.py: _pad_and_merge_cuts`（正典） | 每側最多外吃 `cut_pad` 秒、左右各自 cap 不互相挪用、夾在鄰近保留卡的語音邊界內、門檻 `1e-6`、會正規化反置／零長度區間 |
| 2 | `app.js: mergeDeletionIntervals`（前端獨有） | 完全不吃 pad、門檻 `0.05`、只把右界夾到「下一張保留卡起點」 |

結果：預覽跳段每段比成品**短 `cut_pad` 秒／側**（預設 0.15），而且 foreign cuts 根本沒進預覽。

### 執行項

| 項 | 內容 | 證據 |
|---|---|---|
| B2-1 | `timeline-core.js` 新增 `padAndMergeCuts(intervals, cards, pad)`，逐條對齊 `_pad_and_merge_cuts`（含 `pad <= 0` 原樣返回的向後相容分支） | `tests/test_cut_merge_frontend_parity.py` 5 測（jsc 實跑真的 JS，與 Python 版逐位元比對） |
| B2-2 | 後端 `episode_io.py:268` 唯讀下放 `cut_pad`（前端不寫回 → 不動 `config.merge` 白名單） | 走查 A1 指紋、A5c round-trip |
| B2-3 | `app.js` 刪掉 `mergeDeletionIntervals`，改呼叫共用核心；`state.cutPad` 從 `/api/episode` 讀入 | 走查 A4a–d、B3a/b |
| B2-4 | `foreignCuts` 併入預覽跳段來源 | 走查 C3a（突變 #14 單點紅） |

驗收數字：`verify_cutpad_preview.py` **26/26 通過**（三階段各自改 `episode.yaml` 並重啟伺服器，
全程用真 seek＋真 play 量瀏覽器實際跳到哪，不讀內部變數）；六個突變（`run_cutpad_mutations.py`）
**全部如預期變紅**、還原後回歸 26/26（明細見 `scripts/cdp-walkthrough/timeline-baseline/MUTATIONS.md` #13～#18）。

走查 A6／C4 直接把「後端 `cut_intervals_from_cfg` 算出的磁碟軸區間 + `subtitle_offset_sec`」
對照「瀏覽器量到的跳段落點」—— 軸換算錯 1.5 秒也會紅。

### 使用者可感知的行為變更（三項，刻意的）

1. **預覽跳段的邊界變寬**：現在每側多跳掉最多 `cut_pad` 秒（預設 0.15），與成品一致。
   併軌前預覽比成品保守，會讓人以為「剪太少」，實際出片才發現多剪了。
2. **移除前端獨有的「右界夾到下一張保留卡起點」clamp**：改用正典的「夾在鄰近保留卡語音邊界內」。
   兩者在多數情況一致，差別出現在刪段後面緊接著另一個刪段時。
3. **影片模式剪的段（foreign cuts）現在預覽也會跳**。併軌前它們只在出片時生效，
   預覽照播 —— 這正是上梯 B1-5「不靜默」想解決的那類落差的下半段。

### 不在本梯（附錄，留給下一梯）

1. **落在保留卡內部的 foreign cut，預覽仍會播過去**：`app.js: nextKeepTime`（:3235-3243）
   的第一個迴圈是「t 落在未刪的保留卡內就直接回 t」，保留卡守門優先於 cut 區間。
   刪卡產生的 cuts 一定與卡界對齊所以不受影響；只有影片模式在卡**內部**剪的段會撞到。
   這是 `nextKeepTime` 自己的第三套機制，要修得先決定「卡」與「時間段」誰是預覽的正典 —— 另一件事。
   → **已在 B3-1 處理**（判定不必拆守門，讓守門看來源即可）。
2. （續上梯）`new:` 開頭的新增卡不進 cuts。
   → **記錄更正**：這一項從頭到尾就不是落差。`api.js:138` 的 `new:` 過濾器**永遠過濾不到東西**，
   它是防禦性過濾而非缺失的功能（理由見下方 B3 的判定 3）。B3-3 改成補測試釘住前提。
3. （續上梯）`api.js: toDiskTime(t)`（:42）仍是無 `audioPath` 守衛、2 位小數的既有版本。
   → **已在 B3-2 處理**（併進 `timeline-core.js: alignShift`，並改 3 位小數）。

---

## B3 專梯：預覽守門的來源判定＋軸位移併軌（2026-09-25 開工，＝上梯附錄三項）

### 勘查結論：「這三件事現在各有幾個地方在管？」

| 題目 | 幾個地方在管 | 證據 |
|---|---|---|
| 「這個時間點該不該跳過」 | **3** — ①`padAndMergeCuts`（正典，含 pad/夾界/合併）②`nextKeepTime` 第一個迴圈的保留卡守門（卡界優先，蓋過 ①）③無 | `app.js:3235-3243` |
| 「顯示軸 ↔ 磁碟軸的位移」 | **3 份抄寫** — ①`app.js:2734-2736`（載入，有 audioPath 守衛）②`api.js:_cutOffset`（有守衛、3 位小數）＋`api.js:toDiskTime`（**無守衛、2 位小數**）③`video-edit-prototype.js:alignShift`（有守衛、3 位小數） | 三處算式逐字重複 |
| 「`new:` 開頭的新增卡會不會進 cuts」 | **不會** — 追完所有寫入 `state.deletions` 的路徑，新增卡全部走不到 | 見 B3-3 |

### 三項各自的判定

1. **附錄第 1 項是真落差，但不是「卡 vs 時間段誰是正典」那麼大**。守門存在的理由寫在原註解裡：
   Whisper word_timestamp 會把保留卡的起點推到前一張刪除卡的結束之前，那時 `t` 同時落在
   保留卡與刪除區間內，不守門就會把使用者還看得到的卡跳掉。但這個理由**只對「刪卡產生的 cuts」成立** ——
   foreign cut 是影片模式刻意在卡**內部**剪掉的段，成品一定沒有它，預覽卻照播。
   所以修法是讓守門**看來源**（foreign cut 不受守門保護），不是拆掉守門。
2. **`toDiskTime` 缺 audioPath 守衛是真 bug**：yaml 留著 `audio_sync_offset` 但沒接外接音檔時，
   載入不位移（有守衛）、存檔卻減回去（無守衛）→ 拖一張卡存一次就漂 `sync_offset` 秒。
   且 `_cutOffset()` 與它化簡後完全同值（`-(audioShift + subtitleOffsetSec)`），本來就該是同一個函式。
3. **附錄第 2 項不是落差，是防禦性過濾**：`api.js:138` 的 `new:` 過濾永遠過濾不到東西。
   正確處置是補測試釘住前提＋更正記錄，不是補功能。

### 執行項

| 項 | 內容 | 驗收 |
|---|---|---|
| B3-1 | `nextKeepTime` 守門加來源判定：`t` 落在 foreign cut 內就不受保留卡守門保護 | CDP：卡內部 foreign cut 播到會跳；卡界對齊的刪卡仍照舊；原 overlap 守門場景不回歸 |
| B3-2 | `timeline-core.js` 新增 `alignShift(st)`；`app.js` 載入、`api.js` 兩處、`video-edit-prototype.js` 全部改用它。`toDiskTime` 一併改 3 位小數 | jsc 單元：三處化簡後同值；pytest round-trip：有 `audio_sync_offset` 無音檔時卡時間不漂 |
| B3-3 | 補測試釘住「`new:` key 不可能進 `state.deletions`」；更正附錄記錄 | 測試紅→綠（突變：把新增卡也丟進 deletions 的路徑打開要紅） |

### 完成狀態（2026-09-25）

| 項 | 內容 | 證據 |
|---|---|---|
| B3-1 | `nextKeepTime` 的保留卡守門改為看來源：`t` 落在 `state.foreignCuts` 內時不受守門保護 | 走查 A5a（卡界對齊的刪卡照舊跳）、B4a/B4b（overlap 守門場景不回歸）；突變 #19／#20 各自單點紅 |
| B3-2 | `timeline-core.js` 新增 `alignShift(st)`；`app.js` 載入端、`api.js` 存檔端兩處、`video-edit-prototype.js` 全部改呼叫它，`toDiskTime` 一併改 3 位小數 | `tests/test_align_shift_frontend_parity.py` 3 測（jsc 實跑 JS 對照後端 `srt_total_shift`，含 10 組 cfg）；走查 C1–C8 端到端 round-trip（顯示值＋磁碟 SRT 毫秒）；突變 #21／#22／#23 |
| B3-3 | `tests/test_new_card_keys_never_deleted.py` 釘住「`new:` 鍵不可能進 `state.deletions`」：13 個寫入點逐條記理由＋三道結構性防線＋存檔端防禦 filter | 6 測綠；五個突變（`run_b3_3_mutations.py`）**各自單點紅**，明細見下 |

驗收數字：`verify_b3_guard_and_shift.py` **27/27 通過**（三階段各自改 `episode.yaml`／`_v2.srt`
並重啟伺服器，全程用真 seek＋真 play 量瀏覽器實際跳到哪）；五個突變（`run_b3_mutations.py`）
**全部如預期變紅**、還原後回歸 27/27（明細見
`scripts/cdp-walkthrough/timeline-baseline/MUTATIONS.md` #19～#23）。

B3-3 的突變對照（`b3_3_mutations_result.json`）：

| 突變 | 打開的路徑 | 實際變紅 |
|---|---|---|
| N1 | 框選不再排除 `new:` 鍵 | `test_marquee_filters_new_card_keys` |
| N2 | `renderCards` 的新增卡分支不再 `continue` | `test_render_cards_skips_new_cards_before_delete_toggle` |
| N3 | 載入順序顛倒（先反推選取才清 `newCards`） | `test_new_cards_cleared_before_cut_derived_selection` |
| N4 | 多一個沒審過的 `state.deletions` 寫入點 | `test_all_deletion_write_sites_are_reviewed` |
| N5 | 拿掉存檔端防禦 filter | `test_api_new_key_filter_is_still_the_last_line_of_defence` |

### 留給下一梯（B3 附錄）

1. ~~**後端會剪掉保留卡的頭，預覽刻意不跳 —— 兩邊仍不一致（走查 B5 已把差量釘成 0.75 秒）**~~
   → **2026-09-25 使用者裁決「修完 commit」，已修**（見下方「附錄第 1 項：夾制」）。
2. **`expandedCards()` 會吐出 `new:` 列，不變式靠載入順序維持**：`app.js:2599` 清空
   `state.newCards` 發生在 `:2755` 由 cuts 反推選取之前，所以 `sel.keys` 不可能含 `new:` 鍵。
   這是結構上偏脆的一環（順序一換就破），已由 `test_new_cards_cleared_before_cut_derived_selection`
   釘住；若哪天要重排載入流程，先看那支測試的失敗訊息。

---

## 附錄第 1 項：刪卡產生的 cut 夾在保留卡語音之外（2026-09-25 完成）

使用者裁決：「修完 commit」。改動已交付集的出片邊界這件事由使用者點頭，不是我自行擴充。

**落差的真正來源**（原本的診斷只講對一半）：舊程式碼**有**夾制，但只夾 `pad` 的延伸
（`rights = [max(cs, e) ...]` 永遠不會早於 `e`），區間本身（卡自己的 start/end）沒夾。
所以只要保留卡的頭落在被刪卡的**肚子裡**，原始區間的右緣就已經咬進保留卡的語音，pad 夾制救不到。

**修法**（前後端逐行對照的同一段，`tests/test_cut_merge_frontend_parity.py` 逐位元比對）：

| 檔案：函式 | 做什麼 |
|---|---|
| `assemble.py:_clamp_cut_to_kept` | 新函式：保留卡的尾巴落在區間內 → 左緣讓位；頭落在區間內 → 右緣讓位 |
| `assemble.py:_pad_and_merge_cuts` | 正規化改成無條件執行（`pad<=0` 也要夾）；分出 `deleted` 清單；夾完歸零 → 印警告並丟段 |
| `timeline-core.js:padAndMergeCuts` | 同一段的 JS 孿生版本 |

**只夾「刪卡產生的」區間**，判準是「區間邊界等於某張被整段涵蓋的卡（2ms 容差）」：

- 影片模式自己框的 foreign cut 不夾 —— 使用者畫的邊界不該被竄改，而且 `cutsToCardSelection`
  的合約寫明 foreign cut「絕不擴寬成整張卡、也絕不丟掉」。
- 連刪相鄰兩張重疊卡 → 兩張都不算保留卡 → 互相不夾，照樣併成整段。
- **夾制放在「消費端」不是「存檔端」**：`episode.yaml` 仍存原始卡區間。若存檔時就夾，
  `cutsToCardSelection`（tol=0.02）會把夾過的區間判成 foreign cut，重載後那張刪掉的卡會復活 —— round-trip 破。
- 病態情境（兩張保留卡把被刪卡的語音整段蓋住）→ 夾完長度歸零 → 印 stderr 警告並不剪這段（失敗不靜默）。

**證據**：`tests/test_cut_merge_frontend_parity.py` 5 passed（新增重疊卡 fixture，隨機產生器
25% 機率製造重疊）、全測試 1034 passed / 1 xfailed、走查 27/27（`B_HI` 9.75→9.00、
`B_OVERLAP` 0.75→**0.0**）、突變 M6／M7 各自單側紅 + 差分測試三個突變都紅（MUTATIONS.md #25）。

### 這次順手抓到、留給下一梯的兩件事

1. **走查 B5 自己有靜默 fallback（已修）**：`ov` 原本先 `if ok_ivB:` 才算，於是突變 M6
   讓 B3 紅、B5 卻因為 `ov` 留在預設 0.0 照樣綠。已改成算不出來就判紅。
2. **`nextKeepTime` 的保留卡守衛已不可達（未動，留給下一梯裁決）**：夾制上線後，
   card-derived cut 不可能覆蓋保留卡語音、foreign cut 又本來就跳過守門 —— 突變 M2
   （整個守門拿掉）現在 **0 紅**。要嘛拿掉這個冗餘機制（「同一件事兩個地方在管」的味道），
   要嘛留著當第二層並補一個真能讓它紅的情境。本梯不動它是範圍紀律，不是忘了。

---

## 附錄第 2 項：偏移欄位的 NaN 靜默清零（2026-09-25 完成）

**開工第一問「幾個地方在管」= 3**：字幕偏移 `#srt-shift-input`、cam B 同步
`#cam-sync-offset-b`、音檔同步 `#audio-sync-offset`，三處各自 `Number(el.value || 0)`。

**落差**：三個都是 `<input type="number">`，使用者打出 `1e`／`--` 這種非數字時，
**`el.value` 回空字串**，與「真的清空」無法分辨 → 既有偏移被**靜默清成 0**，
而 `save_state` 對 0 值會 `pop` 掉整個鍵（`camera_sync_offset` 整組消失）。
原本寫在旁邊的 `Number.isFinite` 警示分支永遠等不到 NaN —— 是死碼。
唯一分得出來的是 `validity.badInput`。

**修法**：三處併成單一讀值器 `app.js: readOffsetInput()`，回 `{ok, value, reason}`；
呼叫端非法就 toast ＋早退，不准 fallback 成 0。`_camModalSavePayload()` 一併移進
`try` 內 —— 它在 try 外 throw 會變成未捕捉例外，按鈕永遠停在「儲存中…」的靜默失敗。

**證據**：`scripts/cdp-walkthrough/timeline-baseline/verify_offset_badinput.py` 42/42
（四態俱全：error／success／empty／loading 收尾）、全測試 1053 passed、
突變 MUT-A（拿掉 badInput 守衛 → 字幕偏移**與** cam 偏移同時紅，兼作「併軌成立」的證據）、
MUT-B（守衛拿掉＋builder 移出 try → 按鈕卡死零 toast）。詳見 MUTATIONS.md 同日條目。

### 留給下一梯（附錄）

1. **toast 會蓋住 topbar 右側按鈕**（本梯量到，未修）：`#toast-container` 固定在右上
   （`top:16px; inset:auto 16px auto auto; z-index:10000`），個別 `.toast` 為了點擊關閉
   而開 `pointer-events:auto`。實測 `#cam-btn` 在 x1029-1103 / y14-46，中心 (1066,30)
   正落在 toast 覆蓋範圍 —— toast 還在的那 4~8 秒（warn 4s／error 8s）
   `elementFromPoint` 回的是 toast，點擊被吃掉、鏡頭視窗打不開，使用者感受是「按了沒反應」。
   與 2026-08-25「絕對定位的把手整片蓋住流內按鈕」同型。
   可能修法：toast 容器下移到不與 topbar 重疊的 y、或改成左下／底部中央、
   或只讓 toast 的關閉鈕吃點擊而本體 `pointer-events:none`。三者都會動到全站 toast，
   要一起決定。走查端目前以 `clear_toasts` 閃避。
2. **`nextKeepTime` 的保留卡守衛已不可達**（上梯留下，仍未裁決）：突變 M2 現在 0 紅。
   拿掉冗餘機制，或留著當第二層並補一個真能讓它紅的情境。
