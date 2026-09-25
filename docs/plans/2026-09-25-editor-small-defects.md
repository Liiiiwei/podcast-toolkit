# F 梯：編輯器三顆小缺陷（快捷鍵清單併軌／鏡頭 tooltip／操作欄寬）

2026-09-25。來源：使用者在「哪些重要可以先做」選了選項 1
（小缺陷先清 → 再拆 app.js）。三項全部來自既有梯次驗收時回填的附錄：

| # | 缺陷 | 出處 |
|---|---|---|
| F1 | 快捷鍵總覽 modal 沒列 `[` `]` 打點與 `P` 循環 | `2026-08-09-time-edit-ux.md` 附錄 |
| F2 | 鏡頭 A/B 鈕 tooltip 語意錯（只看 mapping 存在、不看值） | `2026-08-09-subtitle-card-ui.md` 附錄 |
| F3 | 操作欄 `auto` 寬讓待複查卡文字欄右緣差 18px | 同上 |

**本梯刻意排在 D6（拆 `app.js` → `render.js`）之前**：三顆都動 `app.js` 的
既有段落，先改再拆只要追一個檔；拆完再改要在兩個檔之間對照。

---

## F1：快捷鍵清單併軌（本梯唯一的架構決定）

### 開工第一問「這個資料現在有幾個地方在管？」→ **2 個，而且已經漂移**

1. `index.html:1514-1541` —— `<dl class="shortcuts-list">` 靜態 HTML，11 條。
2. `app.js:1537-1540` —— 時間工具列提示列的字串
   （`←→ 起點｜⌥←→ 訖點｜Shift ×5｜⌘ ×10｜[ ] 設為播放位置｜P 循環`）。

漂移的證據就是這顆缺陷本身：E 梯新增的 `[` `]` 打點與「工具列開著時 P＝切換循環」
只進了提示列，沒進 modal；modal 的 `P` 條目至今仍只寫「試聽目前所在字幕卡」。
**答案 ≥2 → 依 CLAUDE.md 先併軌再加功能**，不是在 modal 裡再手抄一份第三份清單。

### 併軌做法

新增 `web/static/shortcuts.js`，匯出單一資料表 `SHORTCUTS`（每筆：按鍵陣列、說明、
`inToolbar` 旗標）。兩個消費端都從它渲染：

- **modal**：`renderShortcutsModal()` 把 `<dl class="shortcuts-list">` 的內容填出來
  （`index.html` 只留空的 `<dl>` 與一行「內容由 shortcuts.js 渲染」註解）。
- **提示列**：`timeToolbarHintHtml()` 篩 `inToolbar === true` 的項目組字串，
  `app.js:1537-1540` 改為呼叫它。

順帶把漏掉的條目補進資料表：`[` `]` 打點、`P` 在工具列開啟時切換循環、
`⏱ 調時間鈕` 的說明。**只補，不改既有 11 條的文案**（縮小 diff，方便驗收比對）。

> 實作時的偏離（2026-09-25 回寫）：原 modal 第 7 條把 `←`/`→` 與 `⇧`/`⌘`/`⌥` 三個
> 修飾鍵全塞進一條 `<dd>`，而提示列是六條細項。兩邊要「同一份資料推出來」就不可能
> 同時保留兩種粒度，所以把那條拆成六條細項（起點／終點／×5／×10／`[` `]`／`P` 循環），
> 與提示列同粒度。資料表共 17 條（原 11 條中的 1 條拆成 6 條 ＝ 16，再加 `⏱ 調時間鈕`
> 說明 1 條）。驗收條件 F1-a 的「≥14 條」照此改為「= 17 條」。

放獨立檔而不是塞 `app.js` 頂部：`shortcuts.js` 是純資料＋兩個純函式、零 DOM 依賴，
正好是 D6 拆 `render.js` 的暖身樣本（先證明「抽得出去、測得到」）。

### 失敗路徑不准靜默
`renderShortcutsModal()` 找不到 `<dl>` 或資料表為空時，要在 modal 內顯示可見的
錯誤文字（不是留一個空白 modal 讓人以為沒有快捷鍵）。

---

## F2：鏡頭 A/B 鈕 tooltip

### 現況（`app.js:2500-2502`、`:2521-2523`）
兩顆鈕各寫一組三元，判準是 `state.camerasMapping.get(key)` **存不存在**、不看值：

```js
aBtn.title = state.camerasMapping.get(key)
  ? "鏡頭 A：目前鏡頭（已 explicit 標記）"   // ← explicit 標 b 的卡也會走這裡
  : "鏡頭 A：目前鏡頭（沿用前一張）";        // ← 繼承 b 的卡也會走這裡
```

兩顆都錯，各錯兩件事：explicit 標 `b` 的卡上 A 鈕寫「目前鏡頭」（其實 B 才是）
且寫「已 explicit 標記」（標記的是 B 不是 A）；沒有 mapping 但繼承到 `b` 的卡上
A 鈕寫「目前鏡頭（沿用前一張）」（沿用到的是 B）。

### 改法：一條規則吃掉四種狀態
真實狀態是 `eff`（生效鏡頭，含繼承）×「有無 explicit mapping」的四格。A/B 兩顆
共用同一個產生器 `camBtnTitle(which, eff, mapped)`：

- `eff === which` → `鏡頭 X：目前鏡頭（已標記 / 沿用前一張）`
- `eff !== which` → `鏡頭 X：切到 X 鏡頭`

同一件事一個地方產生，不是 A/B 各抄一次三元。**只改 `title` 字串，不碰 className、
不碰 `active` 判定、不碰 click 事件。**

---

## F3：操作欄寬

### 現況
`.card` 是 `grid-template-columns: 18px 66px 1fr auto`（`app.css:2685`），末欄 `auto`。
每張卡各自是一個 grid container，所以末欄寬度**逐卡獨立**：待複查卡多一顆「看過」鈕
→ 末欄 46px，一般卡 28px，文字欄右緣差 18px，長列表掃讀參差。

### 改法
末欄由 `auto` 改為固定寬度，值＝實測的最寬狀態（待複查卡）。
**開工前先用 CDP 量出實際的 28 / 46 兩個數字**，不照抄附錄裡的記載。

> 實作時的偏離（2026-09-25 回寫）：固定寬要寫**兩處**。`.card.card-has-cam`
> （`app.css:3519`，雙機集多一欄放 A/B 膠囊）自己重宣告了一整條
> `grid-template-columns`，而 CSS 的 `grid-template-columns` 是**整條覆蓋**、不是逐欄
> 合併——只改 `.card` 的話，雙機集會整個繞過固定寬，18px 參差原封不動地留著。
> 兩處各是一半，所以驗收要在單機集／雙機集各量一次右緣，突變也要**逐一關兩處**
> （MUT-F3A 關 `.card` 的 58px、MUT-F3B 關 `.card.card-has-cam` 的末欄），
> 否則其中一半是死碼也驗不出來。實測值：末欄 `auto` 時單機集有 28／46 兩種寬度、
> 右緣差 18px；固定 58px 後兩種集都收斂成單一右緣。

---

## 排除範圍（明確不做）

- 拆 `app.js` → `render.js`（D6，本梯之後的獨立梯次）。
- 既有 11 條快捷鍵的文案重寫、快捷鍵行為本身（只動「顯示」不動「綁定」）。
- 鏡頭膠囊的樣式、`active` 判定、點擊切換行為。
- `2026-08-09-subtitle-card-ui.md` 附錄第三項（點關調時間鈕後焦點留在鈕上）——
  計畫明寫的規格內行為，非缺陷。

---

## 驗收清單（CDP 實測，每個 ✓ 綁布林斷言）

走查：`scripts/cdp-walkthrough/timeline-baseline/verify_editor_small_defects.py`
（自管 `serve_podcast.py` :8795；第一輪單機集驗 F1／F3，patch `episode.yaml` 加
`cameras.b` 後重起第二輪驗 F2／F3 雙機。待複查卡靠改 srt 尾字造出，跑完全數還原）。
**結果：26/26 通過**（`verify_editor_small_defects_result.json`）。

- [x] F1-a：modal 的 `<dl>` 渲染出 17 組 dt/dd（見上方偏離說明），且含 `[`、`]`、`P` 循環
- [x] F1-b：提示列 HTML 與**併軌前的字面字串逐字相同**（重構無行為變更；比「與資料表相符」更硬的判準）
- [x] F1-c：資料表清空 → modal 與提示列各留可見錯誤文字，不是空白（失敗路徑不靜默）
- [x] F1-d：`#shortcuts-btn` 與 `?` 鍵各開一次 modal，`<dl>` innerHTML 逐字相同
- [x] F2-a：四種狀態各驗一次 title 字串（四態靠純點擊造出：點 1 卡 B → 2 卡繼承 b；點 3 卡 A → 4 卡繼承 a）
- [x] F2-b：點 A/B 仍切換（`active` class 跟著 carry-forward 走）
- [x] F3-a：單機集／雙機集各量一次，全卡 `.card-text` 右緣收斂成單一 x（參差 0px）；
      另驗點「看過」→「已看過」後右緣不動（舊碼此時整張卡的字右彈 10px）
- [x] F3-b：三顆操作鈕沒被裁、沒溢出操作欄、`elementFromPoint(中心)` 仍是自己
- [x] 突變測試：**四項逐一**（MUT-F1 改一條資料 → modal 與提示列兩個消費端同時變；
      MUT-F2 還原舊三元 → 四態說謊；MUT-F3A `.card` 末欄 auto → 參差 18px；
      MUT-F3B `.card.card-has-cam` 末欄 auto → 雙機集參差回來）
- [x] `/usr/bin/python3 -m pytest -q` 1053 passed（`tests/conftest.py` 的 `EDITOR_JS`
      要補登記 `shortcuts.js`——這是既有守門正確攔到新模組，不是 regression）；
      新增文字無日文假名／簡體字
