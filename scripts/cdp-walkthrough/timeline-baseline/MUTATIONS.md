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

跑法（走查會自行起／收 `serve_podcast.py`，但 headless Chrome 要自己先開）：

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --remote-debugging-port=9331 \
  --user-data-dir=/private/tmp/pt-chrome-b1 about:blank &
/usr/bin/python3 -u verify_b1_cuts.py      # 單跑走查：22/22
/usr/bin/python3 -u run_b1_mutations.py    # 六個突變 + 還原回歸
```

沙盒集在 `/private/tmp/pt-timeline-baseline/`；不在那台機器就先跑同目錄的
`build_sandbox_episode.py` 重建（走查會自己把 `episode.yaml` 改成需要的起點）。

---

## #13～#18 前端／後端刪段合併規則併軌（預覽跳段吃 `cut_pad`）

受測走查：`verify_cutpad_preview.py`（26 項斷言，同一個帶 `subtitle_offset_sec: 1.5` 的沙盒集，
`cut_pad` 在三個階段之間切換，走查自己改 `episode.yaml` 並重啟伺服器——
`Episode.cfg` 只在建構時讀一次，不重啟就量到舊值）。突變由 `run_cutpad_mutations.py`
逐一套用、跑完整支走查、`try/finally` 還原，最後再跑一次證明回到全綠。

三個階段（都用**真 seek＋真 play** 量瀏覽器實際跳到哪，不讀任何內部變數）：

| 階段 | 設定 | 期待的顯示軸跳段區間 |
|---|---|---|
| A | `cut_pad=0.4`＋UI 真點刪除鈕刪卡 #4 | `[8.45, 10.05]`（併軌前的舊規則是 `[8.85, 9.75]`） |
| B | `cut_pad=0`＋同一刪卡 | 退回 `[8.85, 9.75]` |
| C | `cut_pad=0.4`＋只有 foreign cut（顯示軸 9.8–9.95） | `[9.75, 10.05]` |

| # | 突變點（產品碼） | 改成 | 預期變紅 | 實際 |
|---|---|---|---|---|
| 13 | `app.js: state.cutPad = Number(data.cut_pad) \|\| 0;` | `state.cutPad = 0;`（等於併軌前的無 pad 行為） | A4b、A4c、C3a | 23/26，如預期 |
| 14 | `app.js: const raw = [...state.foreignCuts];` | `const raw = [];`（影片模式剪的段預覽不跳） | C3a | 25/26，只有 C3a 紅 |
| 15 | `timeline-core.js:722 Math.max(s - pad, leftLimit, 0)` | `Math.max(s, leftLimit, 0)`（左側不延伸） | A4b | 25/26，只有 A4b 紅 |
| 16 | `timeline-core.js:723 Math.min(e + pad, rightLimit)` | `Math.min(e, rightLimit)`（右側不延伸） | A4b、A4c、C3a | 23/26，如預期 |
| 17 | 同上一行 | `Math.max(e + pad, rightLimit)`（右界不夾在保留卡起點，pad 直接咬進下一張卡的語音） | A4b、A4c、C3a | 23/26，如預期 |
| 18 | `episode_io.py:268` 的 `"cut_pad": float(...)` 整行 | 刪掉（後端不下放，前端永遠拿不到值） | A1 | 1/2，A1 紅後走查依設計中止 |

六個突變全部如預期變紅，還原後回歸 **26/26 通過**。完整輸出見 `cutpad_mutations_result.json`。

三個值得記的點：

- **#14 是最乾淨的單點突變**（25/26，只紅 C3a）—— 證明「foreign cut 也要進預覽跳段」
  這條有自己的斷言在守，不是靠刪卡那幾筆連坐測到的。
- **#16、#17 會連紅三筆是實測結果不是判定放寬**：A4b／A4c／C3a 量的都是「跳完之後停在哪」，
  右界一旦算錯，三個落點會一起往前縮（#16）或一起往後衝（#17）。expect 清單照實測列，
  不是照「這個突變理論上該影響誰」列。
- **#18 靠的是指紋斷言 A1**：走查開場就驗 `/api/episode` 回的 `cut_pad=0.4`，
  對不上直接中止（2026-08-25 教訓「測試站台是副本時會測到舊版」的防呆），
  所以它紅在 1/2 而不是跑完 26 項。

跑法（走查會自行起／收 `serve_podcast.py` 與改還原 `episode.yaml`，headless Chrome 要自己先開）：

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --remote-debugging-port=9331 \
  --user-data-dir=/private/tmp/pt-chrome-cutpad \
  --autoplay-policy=no-user-gesture-required --mute-audio about:blank &
/usr/bin/python3 -u verify_cutpad_preview.py     # 單跑走查：26/26
/usr/bin/python3 -u run_cutpad_mutations.py      # 六個突變 + 還原回歸
```

`--autoplay-policy=no-user-gesture-required` 是必要的：這支走查靠真的 `v.play()` 觸發
`autoSkipDeletedSegments`，被自動播放政策擋下時 probe 會回 `fired:false`，該筆量測直接判無效
（不會靜默當成「沒跳」而誤判成綠）。

---

## #19～#23 預覽守門看來源＋軸位移 `alignShift` 併軌（附錄 B3）

受測走查：`verify_b3_guard_and_shift.py`（27 項斷言，同一個沙盒集，三個階段之間走查自己改
`episode.yaml`／`_v2.srt` 並重啟伺服器，`try/finally` 還原）。突變由 `run_b3_mutations.py`
逐一套用、跑完整支走查、還原，最後再跑一次證明回到全綠。

三個階段（A／B 用**真 seek＋真 play** 量播放頭實際停在哪；C 用**真點 ⏱ 鈕、真點「起點 −0.1s」、
真按儲存**，再回頭讀 `_v2.srt` 的毫秒整數）：

| 階段 | 設定 | 要證明的事 |
|---|---|---|
| A | `cut_pad=0.4`＋foreign cut 塞在保留卡 #4 肚子裡（顯示軸 9.1–9.4） | 落在 cut 裡的 9.20 會跳到 9.40（B3-1 之前保留卡守門優先，整段播過去） |
| B | 刪卡 #4，且 `_v2.srt` 卡 #5 起點被挪到磁碟 7.5 與它重疊 | 守門仍護住保留卡 #5 的語音（9.30 不跳）—— 守門只放行 foreign cut，不是整個拿掉 |
| C | `subtitle_offset_sec=0.123` ＋ yaml 留著 `audio.sync_offset=2.0` 但沒接音檔、`cut_pad=0` | 卡面顯示 `0:07.47`、微調後存檔寫成 `00:00:07,247`、重載讀回 `0:07.37` |

| # | 突變點（產品碼） | 改成 | 預期變紅 | 實際 |
|---|---|---|---|---|
| 19 | `app.js: nextKeepTime` 的 `if (!inForeignCut) { …保留卡迴圈… }` | 去掉 `if`，保留卡迴圈無條件跑（＝B3-1 之前的卡優先守門） | A5a | 26/27，只有 A5a 紅 |
| 20 | 同上一段 | 整個保留卡迴圈刪掉（守門全拿掉） | B4b | 26/27，只有 B4b 紅 |
| 21 | `api.js: _diskOffset` 的 `return -alignShift(state);` | 另抄一份**沒有 `audioPath` 守衛**的位移算式（＝併軌前的既有版本） | C6、C8 | 25/27，如預期 |
| 22 | `api.js: const _ms = (v) => Math.round(v * 1000) / 1000;` | `Math.round(v * 100) / 100`（退回 2 位小數） | C6 | 26/27，只有 C6 紅 |
| 23 | `timeline-core.js: alignShift` 的 `s.audioPath && s.audioSyncOffset` | `s.audioSyncOffset`（守衛掉，載入端與存檔端**同時**掉） | C2、C3b、C4、C8 | 23/27，如預期 |

五個突變全部如預期變紅，還原後回歸 **27/27 通過**。完整輸出見
`verify_b3_guard_and_shift_result.json`。

四個值得記的點：

- **#19 與 #20 互為對照，證明守門是「換判準」不是「拿掉」**：#19 只紅 A5a（foreign cut 播過去）、
  #20 只紅 B4b（overlap 誤傷保留卡語音）。兩筆各有專屬斷言在守，任一方向做過頭都會紅。
- **#23 紅在顯示、不紅在 SRT —— 這是併軌換來的對稱性，也是「SRT round-trip 抓不到」的證據**：
  載入與存檔共用同一個 `alignShift` 之後，守衛一起掉只會讓**顯示時間**整體平移，
  存回磁碟的毫秒（`00:00:07,250`，只差 #22 的取整）幾乎照樣對得回來。
  所以 C 階段必須**同時**有「顯示數字斷言（C2/C3b/C4）」與「毫秒斷言（C6）」，缺一個就有一半的
  退化測不到。#21 之所以要另抄一份無守衛的算式而不是直接刪守衛，就是為了重現併軌**前**的
  非對稱 bug（載入不位移、存檔卻減回去）。
- **C1 是「這個測試不是空轉」的自檢**：先斷言 `/api/episode` 真的下放 `audio.sync_offset=2.0`
  且沒有 `path`，守衛才確實被行使過。少了這筆，`audio` 節點哪天被後端吃掉，
  C2／C6 會因為位移恆等於 0 而全部照綠。
- **A1 指紋斷言擋的是「來源未同步」不是突變**：`inForeignCut`、`_diskOffset` 這些字串在
  #19～#22 裡刻意保留（`void inForeignCut;`），否則走查會在 A1 就中止、看不到後面的紅。
  指紋的職責是 2026-08-25 那條教訓（測到副本舊版），不是替突變把關。

跑法（走查會自行起／收 `serve_podcast.py` 並還原 `episode.yaml` 與 `_v2.srt`，
headless Chrome 要自己先開）：

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --remote-debugging-port=9331 \
  --user-data-dir=/private/tmp/pt-chrome-b3 \
  --autoplay-policy=no-user-gesture-required --mute-audio about:blank &
/usr/bin/python3 -u verify_b3_guard_and_shift.py   # 單跑走查：27/27
/usr/bin/python3 -u run_b3_mutations.py            # 五個突變 + 還原回歸
```

C 階段開 ⏱ 工具列會觸發 `startTimeLoop()`（自動 `v.play()` 循環試聽），走查在點完之後
立刻 `v.pause()` —— `onTimeLoopTick` 在暫停時會提早返回，暫停掉才量得到乾淨的輸入框值。

---

## #24 `new:` 卡不可能進 `state.deletions`（B3-3，突變驅動的是 pytest 不是走查）

受測對象不是瀏覽器行為而是**原始碼層的不變式**：
`tests/test_new_card_keys_never_deleted.py`（6 測）。它的職責是「前提被改破就紅」，
所以突變也不是改回舊 bug，而是**把「新增卡也會進 deletions」的路徑打開**。

| 突變 | 檔案：改成 | 預期變紅 | 實際 |
|---|---|---|---|
| N1 | `timeline.js`：框選 filter 拿掉 `!String(r.key).startsWith("new:")` | `test_marquee_filters_new_card_keys` | ✅ 單點紅 |
| N2 | `app.js`：`if (r.newCard)` 分支拿掉 `continue`（往下走到刪除鈕） | `test_render_cards_skips_new_cards_before_delete_toggle` | ✅ 單點紅 |
| N3 | `app.js`：`state.newCards = [];` 搬到「由 cuts 反推選取」之後 | `test_new_cards_cleared_before_cut_derived_selection` | ✅ 單點紅 |
| N4 | `app.js`：多一個沒審過的 `state.deletions.add(r.key)` 寫入點 | `test_all_deletion_write_sites_are_reviewed` | ✅ 單點紅 |
| N5 | `api.js`：拿掉存檔端的 `new:` 防禦 filter | `test_api_new_key_filter_is_still_the_last_line_of_defence` | ✅ 單點紅 |

值得記的兩點：

- **這支測試刻意是原始碼層的變更偵測，不是行為測試**。要證明的是「某件事永遠不發生」，
  行為測試最多只能證明「我試過的那幾條路沒發生」。保證來自三個結構性事實
  （renderCards 的 `continue`、marquee 的 filter、載入時先清 `newCards`），
  所以測的就是那三件事本身，以及「寫入點清單有沒有多出沒審過的第四條路」。
- **N4 是這組裡最重要的一個**：前三個突變證明三道防線各自有人看著，
  N4 證明「有人新開一條沒防線的路」也會被抓到 —— 少了 N4，這支測試只能防退化、不能防新增。

跑法（在 repo 根目錄，會自行備份／還原三支 JS 到 `/private/tmp/b3-3-mutate-bak`）：

```bash
/usr/bin/python3 -m pytest -q tests/test_new_card_keys_never_deleted.py   # 6 passed
python3 -u scripts/cdp-walkthrough/timeline-baseline/run_b3_3_mutations.py
```

---

## #25 刪卡產生的 cut 夾在保留卡語音之外（B3 附錄 1 的落差修掉）

修的是走查原本釘成「已知落差」的那 0.75 秒：相鄰字幕卡的時間戳重疊時（Whisper 逐字
時間戳的常態），被刪卡的尾巴其實是下一張保留卡的開頭語音。原本只有 **pad 延伸**被夾在
保留卡邊界內，**區間本身**沒夾 → 後端真的剪掉保留卡的頭，前端預覽卻靠守衛刻意不跳，
兩邊對不上。修法是前後端同時把「刪卡產生的」區間本身夾到保留卡語音之外
（`assemble.py:_clamp_cut_to_kept` ↔ `timeline-core.js:padAndMergeCuts`），
foreign cut（影片模式自己框的區間）一律不夾 —— 使用者畫的邊界不該被竄改。

走查 B 階段的期望值同步改：`B_HI` 9.75 → **9.00**、B4a 目標 → 9.00、
`B_OVERLAP` 0.75 → **0.0**（B5 從「已知落差」變成「夾制生效」）。

| 突變 | 檔案：改成 | 預期變紅 | 實際 |
|---|---|---|---|
| M6 | `assemble.py`：`ns, ne = s, e`（後端不夾） | B3（後端區間）＋B5（侵入量退回 0.75） | ✅ 2 紅 |
| M7 | `timeline-core.js`：右緣讓位那行改 `if (false)`（前端不夾） | B4a（預覽跳到 9.75） | ✅ 1 紅 |
| 差分 | `assemble.py` 整個夾制關掉 | `test_cut_merge_frontend_parity.py` 固定 case #12＋隨機 case | ✅ 2 紅 |
| 差分 | `timeline-core.js` 只關左緣夾制 | 固定 case #16（夾殺→丟段）＋隨機 case | ✅ 2 紅 |
| 差分 | `timeline-core.js` 只關右緣夾制 | 固定 case #12＋隨機 case | ✅ 2 紅 |

三件值得記的事：

- **M6／M7 的紅項不同，這正是要的**：M6 只紅後端那條（B3／B5），M7 只紅預覽那條（B4a）。
  兩邊各自被獨立釘住，任一側偷偷漂走都有人喊。
- **M6 順手抓到走查自己的靜默 fallback**：B5 原本算 `ov` 前先 `if ok_ivB:`，於是
  M6 讓 B3 紅、B5 卻因為 `ov` 留在預設 0.0 而照樣綠。已改成算不出來就判紅
  （`ov = None` → fail），這才是「失敗不靜默」。
- **夾制上線後 M2（整個守門拿掉）變成 0 紅**：card-derived cut 已經不可能覆蓋保留卡語音，
  foreign cut 又本來就跳過守門 → `nextKeepTime` 的保留卡守衛實際上已不可達。
  本梯不動它（範圍紀律），寫進計畫檔附錄留給下一梯裁決：拿掉冗餘機制，或留著當第二層
  並補一個真能讓它紅的情境。M1（退回卡優先守門）仍單點紅 A5a，所以 B3-1 的行為還有人守。

差分測試新增的重疊卡 fixture：`tests/test_cut_merge_frontend_parity.py` 的
`OVERLAP_CARDS`（卡 #3 的頭往回咬進被刪卡 0.75s）、`SANDWICH_CARDS`（兩張保留卡把被刪卡
的語音整段蓋住 → 夾完歸零 → 丟段，後端另外印警告），隨機產生器也有 25% 機率讓下一張卡
的頭往回咬 —— 沒有這些 fixture，夾制在差分測試裡是完全沒被走到的死碼。

跑法：

```bash
/usr/bin/python3 -m pytest -q tests/test_cut_merge_frontend_parity.py   # 5 passed
/usr/bin/python3 -u verify_b3_guard_and_shift.py                        # 27/27
/usr/bin/python3 -u run_b3_mutations.py                                 # M1～M7
```
