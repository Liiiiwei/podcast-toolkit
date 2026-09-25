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

---

## 2026-09-25 D2-FE 字幕樣式面板（`verify_caption_style_panel.py`，36/36）

後端早就吃得下 `subtitle_style` 九個鍵，只有字級有 UI；這梯把另外 8 個接出來。
UI 改動的證據只能是瀏覽器實測數字，所以走查全程量 `#caption-overlay` 與
`.caption-line` 的 computed style，不看 diff：

- **T2 success 態**：字型堆疊第一順位、文字色 `rgb(255,255,0)`（面板 hex → ASS `&H0000FFFF`
  → CSS 兩次轉換沒走樣）、`fontWeight 400`、12 向描邊皆藍、`border_style=3` 時
  `.caption-line` 底色 `rgb(0,0,255)` 且 overlay `textShadow: none`、
  `alignment=6` ＋ `margin_v=200` → `top` 實測 **18.52%**（＝200/1080），且不設 bottom／transform。
- **T4 error 態**：超限 5000 與真鍵盤打 `1e`（`validity.badInput`）都要顯示錯誤、標紅、
  **且 margin_v 維持 200** —— 證明 NaN 沒被靜默清成 0。
- **T5 empty 態**：樣式為 null → 欄位收起＋說明顯示＋disabled，且能回到 success（不是單向門）。
- **T6 round-trip**：存檔 → 讀磁碟 yaml → **重啟伺服器**＋重載頁面 → 欄位與預覽都讀回原值。

| 突變 | 檔案：改成 | 預期變紅 | 實際 |
|---|---|---|---|
| MUT-A | `api.js`：`SUBTITLE_STYLE_KEYS` 註解掉 `"margin_v"` | 存檔後 yaml 的 margin_v 停在舊值 | ✅ 紅（200，還原後 320） |
| MUT-B | `app.js`：`commitCaptionStyleField` 非法值的 `return` 拿掉 | 5000 被寫進 state | ✅ 紅（5000，還原後 320） |

兩件值得記的事：

- **MUT-A 一定要配對照鍵**。`save_state` 的區塊是從既有磁碟值起算，payload 少送一鍵時
  該鍵會**保留舊值**而不是被刪 —— 光看「margin_v 沒變」無法分辨「白名單漏了」與
  「整個存檔根本沒成功」。所以同一次存檔另外改 `outline`（仍在白名單）當對照：
  RED 時 outline 存成 5、margin_v 停在 200，兩者並排才是證據。
- **`elementFromPoint` 的疊層斷言不能比 id 全等**。`#cap-style-btn` 與 `#save-btn` 都把
  標籤包在 `<span>` 裡，最上層元素本來就是那個 span，點擊照樣冒泡到按鈕。
  要擋的是「別的疊層蓋在上面」，判準是 `el.closest(selector)` 命中。

跑法（Chrome 用全新 profile 起在 :9522，避開舊 profile 媒體快取毀損的既知坑）：

```bash
CDP_PORT=9522 /usr/bin/python3 -u verify_caption_style_panel.py   # 36/36
/usr/bin/python3 -m pytest -q                                      # 1053 passed, 1 xfailed
```

---

## 2026-09-25 偏移欄位 NaN 靜默清零（`verify_offset_badinput.py`，42/42）

三處偏移欄位（字幕偏移 `#srt-shift-input`、cam B 同步 `#cam-sync-offset-b`、音檔同步
`#audio-sync-offset`）都是 `<input type="number">`。使用者打出 `1e`／`--` 這種非數字時，
**`el.value` 回的是空字串**，與「真的把欄位清空」完全無法分辨 —— 舊碼 `Number(value || 0)`
於是把既有偏移**靜默清成 0**（`save_state` 對 0 值會 `pop` 掉整個鍵），而原本那行
`Number.isFinite` 警示永遠等不到 NaN，是死碼。唯一分得出來的是 `validity.badInput`。

修法：三處併成**單一讀值器** `app.js: readOffsetInput()`（回 `{ok, value, reason}`），
呼叫端非法就顯示 toast 並早退，不准 fallback 成 0。

- **T1.3 與 T3.0 並列是整份走查的核心證據**：`1e` 與真清空兩者 `el.value` 同為 `""`，
  只有 `badInput` 一個 true 一個 false —— 「為什麼非得看 validity」這件事因此是可量測事實。
- **T6.4 是防迴歸斷言**：第一版守衛寫成 `!el.validity.valid`，那會把 `stepMismatch`
  一起算成非法 —— step=0.01 的欄位手打 `0.425` 本來收得下，新守衛卻擋掉，是新的退步。
  判準改成只看 `Number.isFinite`，並補這條斷言釘住「step 不整除的合法值仍存得進去」。

| 突變 | 檔案：改成 | 預期變紅 | 實際 |
|---|---|---|---|
| MUT-A | `app.js: readOffsetInput`：拿掉 badInput 守衛三行（退回「空字串當 0」） | 字幕偏移**與** cam 偏移同時被清零、兩條 toast 都消失 | ✅ 4 紅（sub=None、cam=None、toast 0/0；還原後 1.5／0.42／1/1） |
| MUT-B | `app.js: #cam-save`：拿掉兩道早退守衛，且 `_camModalSavePayload()` 移出 `try` | 例外沒人接 → 按鈕卡在「儲存中…」且零 toast | ✅ 3 紅（disabled=True／text='儲存中…'／toasts=0） |

三件值得記的事：

- **MUT-A 同時是「併軌成立」的證據**：同一個突變點讓字幕偏移與 cam 偏移**一起**紅，
  代表三處真的共用一個讀值器、沒有第二套機制躲在旁邊（專案 CLAUDE.md 開工第一問）。
- **CDP 打真鍵盤時 `keyDown` 不可以帶 `text`**：`keyDown` 與 `char` 都帶 `text` 會各插入
  一次字元（"2.5" 變成 "22.55"），連帶讓 step=0.1 的欄位產生 stepMismatch —— 本梯 11 項紅
  是這一個根因。正確順序：`keyDown`（不帶 text）→ `char`（帶 `text`/`unmodifiedText`）→ `keyUp`。
  另：真 `badInput` 只能用真鍵盤事件打出來，程式化賦值造不出來。
- **toast 壽命會吃掉斷言**：warn 4000ms／error 8000ms 自動消失，而 `wait_yaml_change`
  最長等 15 秒 —— 必須「點擊後 0.6s 先讀 toast，再去等 yaml」，否則讀到 0 個是假紅。

### 順手量到、不在本梯範圍的 UX 缺陷（已寫進計畫檔附錄）

**toast 會蓋住 topbar 右側按鈕**：`#toast-container` 是 `position:fixed; top:16px;
inset:auto 16px auto auto; z-index:10000`，個別 `.toast` 是 `pointer-events:auto`
（為了點擊關閉）。實測 `#cam-btn` 在 x1029-1103 / y14-46，中心 (1066,30) 正落在 toast
覆蓋範圍內 —— toast 還在的那 4~8 秒，`elementFromPoint(1066,30)` 回的是 toast 而不是按鈕，
點擊被吃掉、鏡頭視窗打不開。使用者的感受會是「按了沒反應」，與 2026-08-25 那條
「絕對定位的把手整片蓋住流內按鈕」同型。走查端先 `clear_toasts` 閃避（`open_cam_modal`
第一行，註解已寫明是取樣干擾不是受測行為）。

跑法：

```bash
CDP_PORT=9522 /usr/bin/python3 -u verify_offset_badinput.py   # 42/42
/usr/bin/python3 -m pytest -q                                  # 1053 passed, 1 xfailed
```

---

## 2026-09-25 `nextKeepTime` 保留卡守衛裁決（`verify_nextkeeptime_verdict.py`，13/13）

**裁決：守衛拿掉。** 不是因為它摸不到，是因為它唯一還摸得到的情境裡它是**有害的**。

事情的順序是這樣：2026-09-25 前後端把「刪卡產生的」剪除區間夾在保留卡語音之外之後，
「整個守衛拿掉」這個突變從此 0 紅（B3／B4a／B4b／A5a 全綠）。0 紅只說明「摸不到」，
不等於「拿掉是對的」—— 所以先去找它**唯一還摸得到**的形狀，再據以裁決：

`padAndMergeCuts` 的夾制只讓位給「卡界落在區間內」的保留卡（尾在裡面 → 左緣讓位；
頭在裡面 → 右緣讓位）。若保留卡**整個包住**被刪卡（`cs <= s && ce >= e`），兩個條件
都不成立 → 不夾；pad 也被 `leftLimit`／`rightLimit` 夾死不外擴 → 剪除區間原封不動地
躺在保留卡的語音裡。逐字時間戳把長卡起點往前推時，這個形狀真的會出現。

而在那個形狀裡：**合成端（`assemble.cut_intervals_from_cfg`）沒有對應守衛、照剪**，
守衛卻讓預覽照播那一段 —— 預覽與成品對不上，正是守衛當初要解決的那類問題本身。
一個行為只留一個地方管：夾制是正典（前後端同一套、有差分測試逐位元比對），
`nextKeepTime` 只做區間查表。

場景（沙盒 `_v2.srt` 改一行 + yaml 刪一張卡）：卡 #4 = 磁碟 7.350–8.250（刪掉）；
卡 #5 起點從 8.550 往前挪到 **7.200** → `[7.200, 10.150]` 整個包住卡 #4。
`subtitle_offset_sec=1.5`、`cut_pad=0.4`。後端剪除區間 = `[[7.35, 8.25]]`（N1 實測），
完全落在卡 #5 的語音裡（N2 實測，顯示軸 8.70 ≤ 8.85 且 11.65 ≥ 9.75）。

| 突變 | 檔案：改成 | 預期變紅 | 實際 |
|---|---|---|---|
| MUT-G | `app.js: nextKeepTime`：把 2026-09-25 之前的保留卡守衛（含 `inForeignCut` 例外）原樣貼回去 | 顯示 9.30 停在原地（後端剪掉、預覽照播＝對不上） | ✅ RED after=9.303、GREEN after=9.750 |

三件值得記的事：

- **「突變 0 紅」的正確處置是去找可達情境，不是直接刪、也不是直接留**。這次的結論剛好
  也是刪，但理由換了一個：從「它沒用」換成「它在僅存的情境裡會讓預覽說謊」——
  後者才禁得起下一次回頭看。
- **既有走查的「來源指紋」會跟著裁決翻面**：`verify_b3_guard_and_shift.py:A1` 原本斷言
  「app 有 `inForeignCut`」，裁決後改成斷言「舊守衛那行不在、`nextKeepTime` 還在」——
  指紋是防「來源未同步」的，方向要跟現行正典一致，否則下次會把對的碼判成沒同步。
- **回歸證明夾制獨力守得住**：`A5a`（卡內 foreign cut → 要跳）、`A5d`／`B4b`（保留卡
  語音 → 不跳）三條在守衛拿掉後仍全綠 —— 「跳」與「不跳」兩個方向都有斷言，
  才排除得掉「拿掉守衛後變成整段亂跳」。

跑法：

```bash
CDP_PORT=9522 /usr/bin/python3 -u verify_nextkeeptime_verdict.py   # 13/13
/usr/bin/python3 -u verify_b3_guard_and_shift.py                   # 27/27（回歸，CDP :9331）
/usr/bin/python3 -m pytest -q tests/test_cut_merge_frontend_parity.py  # 5 passed
/usr/bin/python3 -m pytest -q                                      # 1053 passed, 1 xfailed
```

---

## 2026-09-25 toast 覆蓋層吞點擊（`verify_toast_no_block.py`，54/54）

受測的修法有**三個獨立部件**，所以突變逐一關，不是只有「全開 vs 全關」——
只關全部的話，任何一個部件是死碼都看不出來（2026-08-08 教訓）。

| 突變 | 檔案：改成 | 預期變紅 | 實際 |
|---|---|---|---|
| MUT-A | `toast.css: .toast`：`flex-direction: row-reverse` → `row`（✕ 回到右端） | ✕ 壓住 `#drawer-toggle` 中心 | ✅ `['drawer-toggle<toast-close>']` |
| MUT-B | `toast.css: .toast`：`pointer-events: none` → `auto`（本體回來吃點擊） | 本體壓住底下元素中心 | ✅ `['drawer-toggle<toast-body>']` |
| MUT-C | `toast.css: #toast-container`：`inset: auto 16px 16px auto` → 右上 | ✕ 幾何落回 topbar 範圍內 | ✅ `True`（中心被擋 `[]` —— 見下方說明） |
| MUT-ALL | 三者全部還原成舊行為 | `save-btn` 中心被擋（原缺陷） | ✅ `['cam-btn<toast-body>', 'output-menu-btn<toast-body>', 'cancel-btn<toast-body>', 'save-btn<toast-body>']` |
| MUT-GREEN | 三者全部還原回新行為 | 不紅 | ✅ `[]` |

三件值得記的事：

- **MUT-C 單獨關掉時「中心被擋」是空的**，因為本體已不吃點擊、✕ 又在左端，光搬位置
  湊不出受害者。這時若硬要一個「blocked 非空」的斷言，就得放寬其他部件 —— 那等於
  突變之間互相污染。改成量**幾何事實**（✕ 的 rect 與 `.topbar` 的 rect 是否相交），
  部件之間才真正獨立。**突變的斷言不一定要跟主判準同一個指標**，只要它能證明
  「這半在做事」。
- **判準從「中心點」改成「中心＋四角五點」之後，門檻必須跟著重定**：五點取樣抓到
  `trim-suggest-btn` 等元素有一個角被 ✕ 壓到。✕ 是 23×20px 的實體，任何位置都可能與
  底下元素的邊緣相交 —— 這是結構性下限，不是回歸。所以門檻定為「中心一律可點」＋
  「邊角被壓的一律來自 `<toast-close>` 而非 `<toast-body>`、且不得是 topbar 七顆之一」，
  並且每次都把邊角清單印出來（2026-08-08「重定門檻要先證明下限、不能偷偷放寬」）。
- **`showModal()` 的 inert 會讓「✕ 可點」這條期待永遠紅**：modal 開著時
  `elementFromPoint(任意點)` 一律回該 `<dialog>`，連 (20,20) 也是。這是瀏覽器語意
  不是覆蓋層問題（模擬舊版同樣如此，截圖也證明 toast 繪製在 backdrop 之上）。
  斷言改成「modal 開著時 toast 仍在視窗內且 `visibility:visible`／`opacity:1`」。

跑法：

```bash
CDP_PORT=9341 /usr/bin/python3 -u verify_toast_no_block.py   # 54/54（含突變五段，自動還原 CSS）
```

突變段直接改 `podcast_toolkit/web/static/toast.css` 再改回來，`patch_file()` 以
「命中數必須恰好 1」守門，`finally` 區塊在中途炸掉時也會還原 —— 跑完務必
`git diff --stat podcast_toolkit/web/static/toast.css` 確認乾淨。

---

## 2026-09-25 編輯器三顆小缺陷（`verify_editor_small_defects.py`，26/26）

三顆缺陷（F1 快捷鍵清單併軌／F2 鏡頭 A/B 鈕 tooltip 四態／F3 卡片操作欄固定寬）
彼此獨立，所以突變逐一關。F3 實作後才發現它自己又有**兩個獨立部件**
（`.card` 與 `.card.card-has-cam` 各宣告一次 `grid-template-columns`，CSS 是整條覆蓋
不是逐欄合併），於是突變從三項變四項——只關 `.card` 那半的話，雙機集那半是死碼
也驗不出來。

| 突變 | 檔案：改成 | 預期變紅 | 實際 |
|---|---|---|---|
| MUT-F1 | `shortcuts.js:65,67`：把 `P` 那條的 `hint`／`desc` 改字（模擬「兩處手抄各自漂移」） | modal 與 ⏱ 提示列**同時**變（同一份資料兩個消費端） | ✅ `modalHasLoop=False`、`hintEqFrozen=False` 兩條一起紅 |
| MUT-F2 | `app.js`：`aBtn.title = camBtnTitle(...)` 還原成舊三元（只看 mapping 存不存在） | 繼承態與 explicit 態的 title 說謊 | ✅ card2 B／card3 A／card4 A 三處退回舊字串 |
| MUT-F3A | `app.css:2695 .card`：`var(--card-actions-w)` → `auto` | 單機集文字欄右緣參差回來 | ✅ `spread=18`、`actWs=[28,46]` |
| MUT-F3B | `app.css:3519 .card.card-has-cam`：末欄 `var(--card-actions-w)` → `auto` | **雙機集**右緣參差回來（單機集不受影響） | ✅ 雙機集 `spread>0`、`actWs` 回到多種 |
| MUT-GREEN | 四者全部還原 | 不紅 | ✅ 26/26 |

三件值得記的事：

- **F1 的主判準不是「modal 與資料表相符」而是「提示列 HTML 逐字等於併軌前的字面字串」**
  （`FROZEN_HINT` 常數）。併軌型重構若拿「兩邊自己比自己」當判準，一起錯掉就一起綠；
  把重構前的舊輸出凍結成常數，才真的在測「行為沒變」。
- **F2 的四態是純點擊造出來的，沒有新增測試 hook**：點第 1 卡 B → 第 2 卡靠
  carry-forward 繼承 b；點第 3 卡 A → 第 4 卡繼承 a。判準是 `eff === which`
  （生效鏡頭，含繼承）而不是 mapping 存不存在——舊碼錯的正是這一點。
- **`Episode` 的 cfg 只在 `serve_podcast.py` 建構時讀一次**，所以「加 `cameras.b` 變雙機集」
  必須自管伺服器並重啟（走查因此分兩輪）；srt 每次請求重讀，所以「造待複查卡」
  改檔即可免重啟。這個差別決定走查要不要拆輪，開工前先確認哪些設定是啟動時快照。

跑法：

```bash
CDP_PORT=9522 /usr/bin/python3 -u verify_editor_small_defects.py   # 26/26（含突變五段，自動還原）
/usr/bin/python3 -m pytest -q                                     # 1053 passed, 1 xfailed
```

突變段直接改 `podcast_toolkit/web/static/{app.js,app.css,shortcuts.js}` 再改回來，
`patch_file()` 以「命中數必須恰好 1」守門，`finally` 區塊比對開場快照還原 ——
跑完務必 `git diff --stat podcast_toolkit/web/static/` 確認只剩本梯的正式改動。

---

## 2026-09-25 D6 重構護欄：渲染指紋（`verify_render_fingerprint.py`，136/136）

D6 要把 30 個渲染函式從 `app.js` 搬到新檔 `render.js`。搬動式重構的風險不是
「寫錯邏輯」而是「搬漏一行、漏一個 import」，**讀 diff 證明不了行為不變**。
所以先立一道護欄：對 10 個狀態 × 8 個渲染容器抓正規化後的 `outerHTML` 當指紋，
動刀前錄 baseline、動刀後逐一比對。

正規化只有三條規則（集中在 Python 端 `norm()`）：摺疊標籤間空白、3 位以上小數
統一 `toFixed(2)`、遮掉 `data-last-render-ms`。規則越少，護欄越不容易瞎掉。

| 突變 | 檔案：改成 | 預期 | 實際 |
|---|---|---|---|
| R0-STABILITY | 不改碼，同一狀態開兩次新頁 | 正規化指紋必須一致（不穩＝護欄會假紅） | ✅ `True` |
| MASK-R3 | 同一份 HTML 的 `data-last-render-ms` 換成 `999.9` | raw 必須不同、正規化後必須相同 | ✅ 兩條都 `True` |
| MUT-R1 | `reviewReasonLabel`：`half_sentence: "半句結尾"` → `"半句結尾ZZ"` | 只有 `#cards-list` 指紋變 | ✅ `['cards']` |
| MUT-R2 | 同一突變下改用「剝掉所有文字節點與 title」的 loose 指紋 | 過度正規化就看不見這個改動 | ✅ `[]`（瞎掉） |
| MUT-GREEN | 還原 | 指紋回到突變前 | ✅ `True` |

**MASK-R3 是這次補的關鍵一條。** 原本 R0-STABILITY 順便印「raw 是不是也一致」，
但那取決於兩次載入的渲染耗時碰巧撞不撞號（實測曾是 7.0 vs 6.9，也曾完全相同）——
拿運氣當證據等於沒證據。改成直接把耗時值換掉，確定性地證明「遮掉 render-ms」
這條規則是承重的：不遮，護欄會因為一個純計時數字恆紅。

### 護欄真的抓到東西

抽完檔第一次跑：**126/136，10 個狀態的 topbar 指紋全紅**（427 → 384 字元）。
`verify_render_fingerprint_diff/S1-topbar-got.html` 顯示版本標籤停在
`<span id="version-label" title="讀取版本中…">版本 …</span>` 沒被更新。
根因：`renderVersionLabel` 仍被 `probeVersion()` 呼叫 5 次，但漏了 export/import，
ReferenceError 被 `probeVersion()` 每個分支的靜默 `return` 吞掉——
**「失敗路徑不准靜默」的活教材**。補上 export 後 136/136。

### 已知覆蓋缺口與補法

`renderCardSkeletons` 也漏了 export，但**指紋護欄一條都沒紅**：10 個狀態全是
「卡片已經回來」之後的畫面，沒有一個落在 loading 時點上。它是靠「拿動刀後的
app.js 當地面真相、逐一 grep 30 個符號」的靜態核對抓到的
（判準：用量 > 0 且 import = 0 → 缺；用量 = 0 且 import > 0 → 多餘）。

缺口已補成獨立走查 `verify_render_skeleton_loading.py`（2/2），
其突變是把 `export function renderCardSkeletons` 改回 `function renderCardSkeletons`，
實際輸出 `SyntaxError: does not provide an export named 'renderCardSkeletons'`，
還原後回綠——正是當天漏掉的那個缺陷。

教訓：**指紋護欄只保護它取樣到的那些時點**。它證明不了沒取樣到的狀態，
所以搬動式重構要同時有「動態指紋」與「靜態符號核對」兩道，缺一會漏。

跑法：

```bash
CDP_PORT=9522 /usr/bin/python3 -u verify_render_fingerprint.py        # 136/136（含突變，自動還原）
CDP_PORT=9522 /usr/bin/python3 -u verify_render_skeleton_loading.py   # 2/2
/usr/bin/python3 -m pytest -q                                         # 1053 passed, 1 xfailed
```

---

## D6 第二刀（2026-09-25）：renderTrimControls ＋ 字幕樣式面板渲染四件

搬走 116 行（`renderTrimControls`、`assColourToHex`、`CAP_STYLE_FIELDS`、
`setCaptionStyleError`、`renderCaptionStyleControls`），**app.js 新增 export 0 個**
——把互相引用的符號整組搬，耦合面就不用擴。

### 動刀前先補護欄（上一刀的教訓直接複用）

第一刀的缺口是「時點」沒取樣到（loading 態）；這一刀開工先查，發現是「容器」
沒取樣到：8 個容器裡沒有任何一個蓋到 trim 控制列或字幕樣式面板。補成 13 個
HTML 容器（`.trim-controls`、`#trim-band-head/tail`、`#trim-handle-head/tail`、
`#cap-style-panel`）＋ 2 個新狀態：

- **S11 capstyle-open**：點「更多樣式」→ `renderCaptionStyleControls` 填滿 9 個欄位。
- **S12 trim-head-set**：設頭 → `renderTrimControls` 的 `head > 0` 分支（色帶／把手／保留提示）。

重錄 baseline 後比對舊檔：**原 10 狀態 × 8 容器的指紋逐字不變**，證明重錄沒把東西藏掉。

### 合成指紋鍵 `capStyleVals`：outerHTML 看不見表單的值

`<input>`／`<select>` 的 `value`／`checked` 是 IDL 屬性，**設定後 outerHTML 一個字都不會變**
（只有 `disabled` 會反映）。字幕樣式面板九成的輸出就是這些活值——只抓 HTML 等於沒測。
所以另立合成鍵，把面板內每個欄位串成 `id=value|disabled`。

### 突變

| 編號 | 改什麼 | 預期 | 實際 |
|------|--------|------|------|
| MUT-R3 | `renderTrimControls` 的預設提示字串改成 `"MUT-R3"` | 只有 `trimCtl` 紅 | 11 個狀態的 `trimCtl` ＋ S1 非退化斷言紅；S12 走 `head>0` 分支所以不受影響（正確）｜還原後全綠 |
| MUT-R4 | `renderCaptionStyleControls` 的色碼欄改成寫死 `#123456` | 只有 `capStyleVals` 紅 | 12 個狀態的 `capStyleVals` 紅，**`capStyle` 的 HTML 指紋全綠**——正是合成鍵要補的盲點｜還原後全綠 |

### S12 的不穩定源（走查自己的 bug，不是產品碼）

第一次錄 baseline 時 S12 斷言 `got='3.7s' want='3.0s'`。根因：S3 開過 ⏱ 工具列會啟動
循環播放，`seek(3.0)` 後那 0.8 秒等待期間播放頭自己往前跑，而設頭鈕讀的是當下
`video.currentTime`。修法是 S12 先 `pause()` 再 seek，並加一條前置斷言確認播放頭真的停在 3.0。
**會漂的量不能直接當指紋輸入**。

### 驗收數字

護欄 **254/254**（12 狀態 × 15 指紋 ＋ 74 條斷言），`verify_render_skeleton_loading.py` 2/2，
`/usr/bin/python3 -m pytest -q` **1053 passed, 1 xfailed**。
靜態符號核對：4 支搬走的符號在 app.js 都有 import、`assColourToHex` 在 app.js 用量歸 0。
`app.js` 7756 → 7645 行，`render.js` 613 → 741 行。
