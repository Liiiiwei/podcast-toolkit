# 突變測試紀錄（真突變，非活性檢查）

> 依據 2026-08-25 教訓「走查印✓只證明沒拋例外」與 judgment-rubrics 第 5 節：
> 每支斷言都要能被「把受測產品碼改回舊行為 → 該斷言變紅」證明真的在測東西。
>
> 本檔區分兩種檢查，兩者不可混稱：
> - **活性檢查（liveness）**：只改「輸入」（多按幾次 zoom、挪動卡片），確認輸出不是死碼/固定值。
>   走查裡 B8 / C4 / E3 等即屬此類，名稱已從舊「突變測試」正名為「活性檢查」。
> - **真突變（mutation）**：把「受測的產品碼那一行」改回舊 bug 行為，確認對應斷言變紅、還原後變綠。
>   這才是證明斷言有效的鐵證。以下記錄 #1、#2 兩個高風險修法的真突變，含實際 RED 輸出。

跑法前提：`serve_podcast.py`(:8795) 服務 **live working tree**（已用 break-red 哨兵證明，見報告），
改產品碼後免重啟、免搬檔即生效；每次突變後務必還原再驗回綠。

---

## #1 標題卡軌 podcast 預設「關」（D1：podcast 預設關、影片預設開）

- 受測斷言：`drive_podcast.py`
  - `E0`：帶 `?pthook=1` 時，`#tl-card-track` 預設 hidden=true、display:none、offsetHeight==0
  - `H1`：不帶 flag 的正常頁面，同樣預設關
- 突變點（產品碼）：`podcast_toolkit/web/static/index.html:332` —— 移除 `#tl-card-track` 的 `hidden` 屬性
  （還原舊行為＝軌道預設顯示）。

### 突變後 RED 輸出（節錄）
```
[FAIL] E0 #1 預設態：#tl-card-track 預設「關」（hidden=true、display:none、不佔高度）
       got={'hidden': False, 'offsetHeight': 44, 'display': 'block'} want=None
[FAIL] H1 #1：正常頁面（無 flag）標題卡軌仍預設「關」（hidden=true、不佔高度）
       got={'hidden': False, 'offsetHeight': 44, 'display': 'block'} want=None
44/46 通過
```
還原 `hidden` 後 → 46/46 全綠（見報告主結果）。

---

## #2 標題卡軌寬度隨 zoom 縮放（縮放施加在共用容器 #tl-tracks）

- 受測斷言：`drive_podcast.py`
  - `Z1`：zoom-in 2 級後 `#tl-tracks` 寬度放大 ≈2.56×（1.6²）
  - `Z2`（**核心回歸點**）：zoom>1 下 `#card-timeline` 與 `#tl-card-track` 仍等寬且左緣對齊（≤1px）
  - `Z3`：標題卡軌寬度確實隨 zoom 變大（非固定值）
- 突變點（產品碼）：`podcast_toolkit/web/static/timeline.js:293` —— 把 `$("#tl-tracks")` 改回 `$("#card-timeline")`
  （還原舊 bug＝縮放只施加在字幕軌，標題卡軌停在原寬）。

### 突變後 RED 輸出（節錄）
```
[FAIL] Z1 #2：zoom-in 2 級後 #tl-tracks 寬度放大 ≈2.5600×（縮放施加在共用容器）
       got=1.0 want=2.56
[FAIL] Z2 #2：zoom>1 下 #card-timeline 與 #tl-card-track 仍等寬且左緣對齊（≤1px，核心回歸點）
       got={'dLeft': 0, 'dWidth': 1457.031} want=None
[FAIL] Z3 #2：標題卡軌寬度確實隨 zoom 變大（非固定值；舊 bug 會停在原寬）
       got=934 want='>1868.0'
43/46 通過
```
還原 `#tl-tracks` 後 → 46/46 全綠。Z2 的 `dWidth=1457px` 直接量出舊 bug 的破版幅度：
字幕軌被拉寬、標題卡軌沒跟上，兩軌左緣雖同、右緣差 1457px。

---

## 影片模式（drive_video_proto.py）

影片模式對應的縮放本來就施加在共用容器 `#vt-tracks`（B2/B7 已測），非本次修法標的；
其走查內原名「突變測試」的 B7/C4/E3 均為活性檢查，已正名。影片模式真突變不在本梯範圍
（本梯只碰 podcast 的 #1/#2 產品碼）。

---

## #3 影片模式 baseline：addCut 重疊合併判斷（W2/W3 前的煙霧測試基準）

- 受測斷言：`verify_video_baseline.py` 的 V4.5/V4.6（重疊段合併成 `cuts==[[2,8]]`、`cutCount==1`）。
- 突變點（產品碼）：`video-edit-prototype.js` 的 `addCut(a,b)` 內、重疊合併分支條件
  `if (cur[0] <= last[1])` → 暫時改 `if (false)`（還原舊 bug＝重疊段不合併）。
- 突變後 RED 輸出（節錄）：
  ```
  [FAIL] MUT1-RED 重疊本應合併成 cutCount==1（突變後預期會錯）  got=2 want=1
  ```
  還原後重跑 → `cuts==[[2,8]]`、`cutCount==1` 恢復綠燈。

## #4 影片模式 baseline：applyStyle 的 bold→fontWeight 綁定

- 受測斷言：`verify_video_baseline.py` 的 V5.5（點擊粗體「關」→ 預覽 `fontWeight==400`）。
- 突變點（產品碼）：**W2 後已搬到共用核心** `timeline-core.js` 的 `buildSubtitleCss()` 內
  `fontWeight: Number(st.bold) ? "700" : "400",` → 暫時改成固定 `"700"`（還原舊 bug＝
  fontWeight 不跟隨 `state.style.bold`）。突變點跟著受測邏輯一起搬，才能證明影片模式
  現在真的是吃共用核心那份換算，而不是自己留了一份。
- 突變後 RED 輸出（節錄）：
  ```
  [FAIL] MUT2-RED bold=0 應反映 fontWeight==400（突變後預期會錯，仍卡在 700）  got='700' want='400'
  ```
  還原後重跑 → `fontWeight` 恢復 `"400"`，綠燈。

以上兩個真突變的完整 RED/GREEN 週期已內建於 `verify_video_baseline.py` 本身（每次執行都會
自動改檔→重載→驗紅→還原→重載→驗綠，try/finally 保證還原），不需要手動操作即可重現。

---

## #5 podcast 字幕預覽真的吃 episode.yaml 的 subtitle_style（W2 併軌）

W2 前 podcast 預覽只算字級，顏色／描邊／粗體／底色塊／垂直落點全寫死在 `app.css` 與
`renderCropInfo()`（「Reels 置中、YT 置底 8%」）—— 改了 `subtitle_style` 預覽不會動，
預覽 ≠ 成品。W2 把換算收斂成共用核心的 `buildSubtitleCss()` 一份，podcast 也吃它。

- 受測斷言：`verify_podcast_caption_style.py` 的 P1.5 / P1.6（文字與行級顏色＝`primary_colour`）。
- 突變點（產品碼）：`app.js` 的 `applyCaptionStyle()` 內
  `_captionCss = scale == null || !style ? null : buildSubtitleCss(style, scale);`
  → 暫時只保留 `fontSize` 一個鍵（還原舊行為＝只算字級，其餘交給 app.css 寫死）。
- 突變後 RED 輸出：
  ```
  [FAIL] MUT1-RED 文字顏色應為 primary_colour（突變後預期退回 app.css 白）
         got='rgb(255, 255, 255)' want='rgb(255, 255, 0)'
  ```
  還原後 → `rgb(255, 255, 0)`（yaml 設的黃）恢復，綠燈。RED 值正好是 `app.css` 的保底白，
  等於量到「舊行為長什麼樣」。

## #6 podcast 字幕垂直落點由 alignment/margin_v 決定（不再寫死）

- 受測斷言：`verify_podcast_caption_style.py` 的 P2.1 / P2.2（`alignment=6` → `top≈5.56%`、
  `bottom` 空）。
- 突變點（產品碼）：`app.js` 的 `renderCropInfo()` 內
  `const capBucket = subtitleAlignmentBucket(capStyle?.alignment);`
  → 暫時改回寫死 `state.activeVersion === "reels" ? "middle" : "bottom"`（還原舊 bug）。
- 突變後 RED 輸出：
  ```
  [FAIL] MUT2-RED alignment=6 應置頂（top 有值、bottom 空）
         got={'top': False, 'bottom': '5.56%'} want={'top': True, 'bottom': ''}
  ```
  還原後 → `top='5.56%'`、`bottom=''`，綠燈。

#5/#6 的 RED/GREEN 週期同樣內建於 `verify_podcast_caption_style.py`（改檔→重載→驗紅→
還原→重載→驗綠，try/finally 保證還原）。該走查另外自管 `serve_podcast.py` 子行程：
樣式變體要改 `episode.yaml`，而 `Episode.cfg` 只在建構時讀一次，所以每個變體重啟一次伺服器，
跑完把沙盒 `episode.yaml` 還原成原樣。

---

## #7～#12 B1：剪除語意單一 source of truth（時間版 cuts 為正典）

受測走查：`verify_b1_cuts.py`（22 項斷言，沙盒集刻意帶 `subtitle_offset_sec: 1.5`，
讓「顯示軸 ↔ 磁碟軸」的換算真的被走到）。突變由 `run_b1_mutations.py` 逐一套用、
跑完整支走查、`try/finally` 還原，最後再跑一次證明回到全綠。

| # | 突變點（產品碼） | 改成 | 預期變紅 | 實際 |
|---|---|---|---|---|
| 7 | `timeline-core.js: cardKeysToCuts` 的 `.filter((r) => set.has(r.key))` | `false && …`（存檔不再把刪卡寫成時間段） | 3b | 15/22，3b/4b/4c/5c/6b/6d/7 紅 |
| 8 | `timeline-core.js: cutsToCardSelection` 的 `covered` 判準 | 改成「只要相交就算涵蓋」且跳過外緣對齊檢查（把對不齊的 cut 擴寬成整張卡） | 5b、5c | 17/22，5b/5c/6b/6d/7 紅 |
| 9 | `app.js` 載入端 `if (Array.isArray(data.cuts) && data.cuts.length)` | `if (false && …)`（回到只認 `deletions` 的舊行為） | 4b | 15/22，4b/4c/5b/5c/6b/6d/7 紅 |
| 10 | `app.js: renderTopbar()` 的 `if (foreign > 0)` | `if (false)`（換不回卡的剪段不顯示＝靜默生效） | 5b | 21/22，只有 5b 紅 |
| 11 | `api.js: cutToDiskTime` 的 `[_ms(s + off), _ms(e + off)]` | `[_ms(s), _ms(e)]`（存檔寫成顯示軸、沒減回偏移） | 3b | 14/22，3b/4b/4c/5b/5c/6b/6d/7 紅 |
| 12 | `api.js: cutFromDiskTime` 的 `[_ms(s - off), _ms(e - off)]` | `[_ms(s), _ms(e)]`（載入不加回偏移、對不回卡） | 4b | 15/22，4b/4c/5b/5c/6b/6d/7 紅 |

六個突變全部如預期變紅，還原後回歸 **22/22 通過**。完整輸出見 `b1_mutations_result.json`。

兩個值得記的點：

- **#10 是最乾淨的單點突變**（21/22，只紅 5b）—— 證明「foreign cut 要看得見」這條
  不是靠其他斷言連坐測到的，它有自己的斷言在守。
- **#11 只差 1.5 秒也會紅**：偏移少減一次，yaml 的 cuts 整批變成顯示軸，3b 的期待值
  `[[2.7,3.9],[7.35,8.25]]` 立刻對不上。這是 `subtitle_offset_sec: 1.5` 這個沙盒設定
  唯一的存在理由 —— 偏移為 0 的集，軸換算寫錯也測不出來。

跑法（會自行起／收 `serve_podcast.py`，需先有 headless Chrome CDP :9331）：

```bash
/usr/bin/python3 -u run_b1_mutations.py
```
