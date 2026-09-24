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
- 突變點（產品碼）：`video-edit-prototype.js` 的 `applyStyle()` 內
  `el.style.fontWeight = st.bold ? "700" : "400";` → 暫時改成固定 `"700"`（還原舊 bug＝
  fontWeight 不跟隨 `state.style.bold`）。
- 突變後 RED 輸出（節錄）：
  ```
  [FAIL] MUT2-RED bold=0 應反映 fontWeight==400（突變後預期會錯，仍卡在 700）  got='700' want='400'
  ```
  還原後重跑 → `fontWeight` 恢復 `"400"`，綠燈。

以上兩個真突變的完整 RED/GREEN 週期已內建於 `verify_video_baseline.py` 本身（每次執行都會
自動改檔→重載→驗紅→還原→重載→驗綠，try/finally 保證還原），不需要手動操作即可重現。
