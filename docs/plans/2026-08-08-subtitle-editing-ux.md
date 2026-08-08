# 字幕編輯 UX 改善（第一梯）

2026-08-08。來源：使用者點名三個痛點 + 全 app UI/UX 盤點。
本檔只涵蓋**已授權執行**的第一梯，第二／三梯的盤點結論放在文末備查。

## 診斷結論

三個抱怨對應四個獨立根因，其中 T1 是 bug 不是設計問題。

| # | 使用者的話 | 根因 |
|---|---|---|
| T1 | 調整字幕時間很難用 | 兩套互不知情的時間 state，⏱ 工具列讀寫的那套在渲染時被壓過 |
| T2 | 吸附很難用、可以移除 | `Math.min(0.3, …)` 讓「像素換算」永遠不生效，snap 半徑恆為 0.3s 死區 |
| T3 | 調整時間的精細度很差 | 不是縮放不足（已有 1×–60×），是顯示精度粗於資料精度、且無數值輸入與快捷鍵 |
| T4 | 字幕卡想要雙行顯示 | 後端／燒字全通，斷在前端三處 |
| W1 | （盤點補充）改完卡片畫面會跳 | `renderCards()` 全量重建，無捲動／焦點保存 |

---

## T1：統一時間 state

### 現況

兩個 Map 各自存字幕時間：

- `state.timeOverrides`（`app.js:31`）← ⏱ 工具列的 ±0.1s、「設到游標」、`reorderCardTo`
- `state.cardTimings`（`app.js:39`）← 時間軸拖把手

渲染時 `cardTimings` 贏（`app.js:866`），但 `getEffectiveCardTime()`（`app.js:909`）只讀 `timeOverrides`。

**後果**：一張卡在時間軸拖過之後，⏱ 工具列顯示的是拖曳前的舊值，按 ±0.1s 畫面不動。
後端 `episode_io.py:626` 註解自承 `time_overrides` 是「舊版拖拉」——這是沒做完的遷移。

### 關鍵細節（決定做法）

兩個 Map 的語意**不完全重疊**，不能無腦合併：

- `timeOverrides.get(c.idx)` 在 `expandedCards()`（`app.js:836`）當作**整卡時間封套**，
  切過句的卡用它當 t0/t1 **按比例重算各子卡**（`app.js:843-847`）。
- `cardTimings` 同時存兩種 key：整卡用 int（`c.idx`），子卡用 composite string（`"5:1"`）。

因此正確做法不是「把 timeOverrides 併進 cardTimings」，而是認清
**「整卡封套」這個概念被重複實作了兩次**，統一到 `cardTimings` 的 int key 上。
`clearCardTimings()`（`app.js:210`）本來就同時清 int key 與所有 `idx:*`，語意天然吻合。

### 改法

前端全面改用 `cardTimings`，退役 `timeOverrides`：

1. `expandedCards()` `:830`、`:836` 封套改讀 `cardTimings.get(c.idx)`
2. `:866` 整卡分支的重複查詢收掉（cStart/cEnd 已是同一個值）
3. `getEffectiveCardTime()` `:909`、`setCardTime()` `:919-933` 改讀寫 `cardTimings`
4. `cardTimeTarget()` `:982-986` 的 reset / isDirty 改用 `cardTimings`
5. `:1899` 時間微調徽章、`:2163` 刪除時的清理同步改
6. snapshot／restore `:166-170`、`:193-198`、unsavedCount `:379`、reset `:2524` 移除 timeOverrides
7. 存檔 payload `:4005` 不再送 `time_overrides`，只送 `card_timings`

**後端不動**：`episode_io.py:629-640` 繼續接受 `time_overrides` payload（向後相容、已有測試覆蓋），
只更新註解標明現行前端不再送。移除它沒有使用者可見效益，卻要動已測過的後端路徑。

---

## T2：移除吸附，改視覺參考線（B2）

### 現況與機制

`app.js:1565`：

```js
const snapSec = Math.min(0.3, (8 / rect.width) * total);
```

註解寫「吸附半徑用像素換算（約 8px），縮放時手感一致」，但這個意圖**從未執行**。
60 分鐘集、容器約 800px 時，8px 換算 = `36 / zoom` 秒；`TL_ZOOM_MAX = 60` → 最小 0.6s，
永遠大於 0.3，`Math.min` 恆取 0.3。

**實際後果**：每個靜音邊界周圍 ±0.3s 是死區，游標一進去就被吸走。
podcast 靜音邊界密集，等於越放大想精修、越在跟吸附角力。Alt 可暫時關閉，但無人知曉。

### 改法

- 移除 snap 區塊 `app.js:1560-1583` 與 readout 的 `·吸附` 標注 `:1624-1627`
- 移除 `app.css:3052` 的 `.tl-drag-readout.snapped`
- **保留靜音邊界的視覺價值**：在時間軸波形層畫細參考線（純視覺，不吸附），
  資料來源沿用既有的 `state.waveform.silences`

---

## T3：時間精度可見化

### 現況

資料精度是 0.01s（`setCardTime` `:915` 四捨五入到 0.01），但畫面上：

- 卡片時間 `fmtTime()`（`:354`）→ `mm:ss`，**連小數都沒有**
- 拖曳 readout `fmtTimeD()`（`:347`）→ 只到 0.1s
- 沒有任何數值輸入框；沒有時間微調快捷鍵（keydown 表 `:303-342`）

對照組：trim 把手已有 ←/→ 0.1s、Shift 0.5s、⌘ 1.0s（`app.js:3580`）。

### 改法

1. 新增 `fmtTimeCard()` → `mm:ss.dd`，套用在三個「卡片時間」顯示點：`:1024`、`:1115`、`:1894`
   （tooltip／播放器時間 `:1264/1311/1697/3160` 維持 mm:ss，那裡不需要 0.01s）
2. ⏱ 工具列加**可直接打字的起／訖時間輸入框**（接受 `12.34` 或 `1:23.45` 兩種寫法）
3. ⏱ 工具列開啟時加時間微調快捷鍵，沿用 trim 已有的慣例：
   ←/→ 0.1s、Shift 0.5s、⌘ 1.0s（起點）；加 Alt 改調終點
4. 補 ⏱ ±0.1s 的**鄰卡重疊檢查**——目前拖曳有夾制（`:1591/1604`）、工具列沒有，兩條路徑行為不一致

---

## T4：手動雙行

### 需求釐清

使用者的成因是「**兩個人同時講話**」→ 兩行 = 兩個講者各一行。
**不設字數門檻、不自動折行**，純手動。

（注意：這跟 `bedfdae` 的 `dual_line.py` 是不同東西。那個是兩張卡上下疊，
本項是一張卡內折成兩行。）

### 後端已經支援

- `srt_io.py:139` 解析多行進 `text`、`:219` 原樣寫回
- `assemble.py:322` `\n` → ASS `\N`；`:289` `WrapStyle: 0`（不自動折行，尊重手動換行）

### 前端斷三處

1. `app.js:2012` `text.textContent.trim()` —— contentEditable 的換行是 `<br>`，`textContent` 取不到 → 換行在 blur 時被靜默丟棄
2. `app.js:2053` 註解說「Shift+Enter 保留原生換行 escape hatch」，但因為第 1 點，這個 escape hatch 從來沒真正生效過
3. `app.css:3059` `.card-text` 缺 `white-space: pre-wrap` → 就算存住了也不會顯示成兩行

### 改法

- 取值改成能還原 `<br>` 的實作（`innerText` 或手動走 childNodes），寫回時 `textContent` 改為設定含換行的節點
- `.card-text` 加 `white-space: pre-wrap`
- 加一顆明確的「折兩行」按鈕（不要只靠隱藏的 Shift+Enter）
- 補測試（目前單卡內換行**零覆蓋**）

---

## W1：捲動＋焦點保存

`renderCards()` 第一行 `list.innerHTML = ""`（`app.js:1777`）整列砍掉重建，
全檔有 25 個呼叫點——刪一張卡／切一次鏡頭／改一個字／undo 都會觸發。

全檔**沒有任何** `scrollTop` 保存邏輯（只有播放跟隨用的 `scrollIntoView`）。
目前是逐案打補丁：切句有 `focusSplitTarget`、時間工具列有 `:2326-2328`——
每加一個功能就要記得補一次，漏掉就跳。

**改法**：`renderCards()` 開頭記住 `list.scrollTop` 與當前 focus 的卡 key，結尾還原。約 20 行通用解。

（完整 windowing = W3，本梯不做。理由：`app.css:2527` 的 `content-visibility: auto`
已經吃掉離畫面卡片的 layout/paint，windowing 的邊際效益只剩 createElement/addEventListener；
而它會讓瀏覽器原生 ⌘F 只搜得到畫面上那 20 張，對校字幕是明確退步。）

---

## 驗收

- `pytest` 全綠（現有 850 passed 基準）
- `app.js` 語法檢查（本機無 node，用 `jsc` + `new Function`）
- T1／T3／T4 各自的行為以 CDP headless 實測，配突變測試（改動停掉時數字要退回舊值）
- T4 補後端測試：帶 `\n` 的卡片存檔 → SRT 多行 → ASS `\N`

### 驗收結果（2026-08-08 實測）

`pytest`：**854 passed, 1 failed**（+4 = 新增的 `tests/test_card_wrap.py`）。
唯一的 fail 是既有問題、與本次無關：`test_api_dashboard.py::test_get_episodes_returns_list`。

> 訂正（同日稍晚）：這個 fail **不是** macOS TCC。真因是 `~/Downloads/20260805 讀者太太`
> 這個空資料夾權限少了 traverse bit（`drw-------`），`dashboard.py:116` 讀它的
> `episode.yaml` 就 `PermissionError`，一支壞資料夾會讓整個 `/api/episodes` 回 500。
> `chmod u+x` 該資料夾後重跑：**10 passed**。附帶問題：這行沒有 try/except，
> 任何一個讀不到的資料夾都會打掉整份集數清單（本梯不修，列進附錄）。

語法檢查：`jsc --module-file app.js` → `ReferenceError: Can't find variable: document`
（無 DOM 環境的執行期錯誤，代表**解析通過**）。

CDP headless 實測（假集 40 張卡＋合成波形，靜音 `[5.0,6.2] [30,31.5] [61,62]`）：

| 項 | 證據 | 突變／反向對照 |
|---|---|---|
| T1 | 拖時間軸把 card 2 終點拖到 5.10 後，⏱ 工具列讀到 `0:03.00 / 0:05.10`（不是舊值 0:05.50） | 兩套 state 若沒統一，工具列會停在拖曳前的值 |
| T2 | 同一次拖曳落在 `0:05.10` —— 距靜音邊界 5.00 只有 0.10s，遠在舊的 0.3s 死區內，沒有被吸走 | 吸附若還在，讀值會是 5.00 |
| T2 參考線 | 「適合」(1×) 取樣 `rgba(0,0,0,0)`；2.6× 同點 `rgba(153,153,166,20)` = `#9b9ba8` α≈0.08 | 低於 `TL_GUIDE_MIN_ZOOM=2` 完全不畫，證明畫的是參考線本身 |
| T3 | 卡片時間顯示 `0:06.00 / 0:08.50`（0.01s）；→ +0.10、⇧→ +0.50、⌘→ +1.00 調起點，⌥→ +0.10 調終點；打字 `0:05.37` + Enter 後 dur 變 2.37s | ↑ 鍵數字完全不動 → 變化來自 ←/→ 而非重繪副作用 |
| T4 | 游標在 offset 5 按「折兩行」→ 文字 `第2張字幕\n卡的內容文字`、高度 26→51px、鈕變「合一行」；`/api/save` payload 的 `cards[idx=2].text` 確實含 `\n`；再按一次合回（h=26） | 把 `white-space` 改成 `nowrap` → 高度掉回 27px，改回 `pre-wrap` → 51px |
| W1 | 四變體隔離：A 全開 `scroll 2674 / idx 30 / caret 3` 全數還原 | B/D（關焦點還原）→ `idx null / caret 0`；C（關捲動還原）→ scroll 飄到 2788 |

W1 的一個實測細節值得記著：`focus({preventScroll:true})` 擋得住 focus 自己，
**擋不住瀏覽器在版面結算後把游標捲進畫面** —— 同步讀與第一幀都還是 2674，
第二幀才跳成 2788。所以 scrollTop 要同步寫一次、再用兩層 `rAF` 補寫一次。
另外實測到 Chrome 在「同一個 task 內清空又填回」的重建下會自己保住 `scrollTop`，
所以捲動還原這段真正在做的事是**抵銷游標造成的位移**，不是防止捲回頂端。

### 真集實測（2026-08-08，`20260522 魁哥`，1437 張卡）

跑的是真的 `podcast-ui.sh` server（port 54160，`/static/app.js` 的 shasum 與 repo 檔一致），
CDP 只做讀取與不落地互動，全程沒點任何儲存鈕；事後確認集數資料夾檔案 mtime 未變。

| 項 | 真集證據 |
|---|---|
| T3 顯示 | 首卡讀到 `0:11.24 / 0:12.68`（0.01s） |
| T3 微調 | card #200：`→` 6:50.37→6:50.47、`⇧→` →6:50.97、`⌘→` →6:51.97；`↑` 對照完全不動 |
| T3 ⌥ 終點 | card #23：`⌥→` 0:47.34→0:47.44、`⌥←` 回 0:47.34（#200 的 `⌥→` 不動是正解 —— 它的終點正貼著下一張起點，被 `clampToNeighbours` 擋住） |
| T3 打字 | 手打 `0:44.38` + Enter → 讀回 0:44.38、卡片轉 `time-dirty`；按「還原」→ 回 0:43.98、dirty 清掉 |
| T2 參考線 | 「適合」1× 取樣 0 個參考線像素；放大到 4.1× 同一區 10680 px 命中 `#9b9ba8` |
| T2 無吸附 | 時間軸左把手逐 px 拖：起點 2786.682→2787.371→2788.058→2788.744，每 px 位移 0.689/0.687/0.686s，與 `secPerPx = 0.6867` 完全吻合（第 5 px 停在 dur=0.100 是 `TL_MIN_DUR` 夾擠，不是吸附） |
| T4 | card #300 游標 offset 4 按「折兩行」→ `現在跑去\n哪裡了那個角度看不到`、高 50→51px、鈕變「合一行」；再按 → 換行消失、高度回 50 |
| W1 | `.cards-pane` 捲到 40000px、焦點在 card #428 的 offset 3 → 刪一張畫面外的卡觸發整列重建 → **scroll 40000、idx 428、caret 3 三項全數還原**（該集 scrollHeight 132994 / clientHeight 717） |

真集上量到的一個事實：47 分鐘的集數在 4.1× 縮放下 1px 仍等於 0.69 秒，
所以時間軸拖拉本質上是粗調；**要 0.1 秒級的精準只能靠 T3 的 ⏱ 鍵盤微調或打字**——
這正好是 T3 存在的理由。

---

## 附錄：本梯不做，但盤點到的問題

### 字幕相關
- 兩個都叫「偏移」但單位不同的輸入：`index.html:518`（秒，cards-toolbar）vs `:132`（毫秒，輸出選單）
- 快捷鍵說明漏列 ⌘+滾輪縮放、trim 的 Shift/⌘ 微調
- 時間軸拖曳的 `rect` 在 `:1521` 只取一次，拖到一半捲動會漂移
- `dashboard.py:116` 沒有 try/except：任一個讀不到的資料夾就讓整份集數清單回 500

### 全 app
- 27 處 `alert()` 當錯誤 UI，無 toast 系統
- 加字典用兩個連續 `prompt()`（`app.js:3018`）
- 倍速／去空拍／封面藏在「按下輸出才跳出」的 modal（`index.html:889/918/928`）
- W3 完整 windowing（1235 卡實例見 TODO.md:13）

### 驗收時另外量到的（2026-08-08，找到但本梯不修）
- **未存檔徽章恆亮**：`unsavedCount()`（`app.js:405`）把 `state.cropYt`／`state.cropReels`
  只要不是 null 就各記 1，而這兩個值一開集就被填好 → 徽章從打開的第一秒就顯示「2」。
  `git show HEAD:...app.js:373` 證實 HEAD 版邏輯一模一樣，是既有缺陷不是本梯造成。
- **`app.js:1564` 註解過期**：寫「沒切的新增卡兩邊都找不到，什麼都不做」，
  但實測新增卡確實有 block（`{"key":"new:0","位置":1437,"總塊數":1438}`），走的是第一個分支。
  程式碼是對的，只有註解錯。
- **新卡把手在「適合畫面」倍率下點不到**：48 分鐘的集數裡 1.45 秒的卡只有 2px 寬，
  7px 的把手整個被鄰塊 `.tl-block` 壓住，`elementFromPoint` 命中鄰塊。
  放到最大倍率（60×）後區塊 29px、把手可命中，功能本身沒壞，是幾何必然。

### 做得好、不要動
四態覆蓋（skeleton/spinner/empty/error）扎實、modal 已全面改用原生 `<dialog>`、
`tokens.css` 已共用、dashboard 三態完整。
