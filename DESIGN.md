# Podcast Toolkit · 設計工作規範

> 給 design-shotgun、新功能 UI、code review 用的設計準則。
> Source of truth：`podcast_toolkit/web/static/tokens.css`。本檔說明「為什麼」與「怎麼用」。

---

## 1. 視覺身分（Visual Identity）

**靈感來源**：DaVinci Resolve、Logic Pro。專業剪輯工具的深色介面，不是 SaaS 儀表板。

**情感目標**：讓使用者覺得自己在用一個「工作站」，而不是一個「web app」。資訊密度高、操作快、視覺不搶戲。

**三個視覺錨點**：

1. **深色三層底**：`--bg #0e0e11` → `--surface #16161a` → `--surface-raised #1f1f24`。任何卡片、modal、按鈕都從這三層挑一個，不要自創。
2. **單一暖橘 accent**：`--accent #ff7849`。只用在主要動作、focus ring、active state、品牌標記。**整頁同時只能有一個 accent 元素競爭注意力**。
3. **字體分工**：IBM Plex Sans 給 UI、JetBrains Mono 給時碼/路徑/集數編號。Mono 字不是裝飾，是「這是機器可讀資料」的視覺暗示。

---

## 2. 六個核心原則（Core Principles）

### P1. Token-first
任何顏色、間距、圓角、陰影、字體、動效時長一律走 `tokens.css` 的 CSS 變數。新增 token 比寫 hex/px 便宜。
**例外**：純結構數值（`width: 100%`、`flex: 1`、`gap: 6px` 這類視覺中性的微調）。

### P2. 三層背景紀律
`--bg` 是頁面底色；`--surface` 是卡片/topbar；`--surface-raised` 是 hover/active 的互動元件；`--surface-sunken` 是 input/code block。**不要混用、不要新增第四層**。

### P3. 單一 accent
一個畫面同時只有一個橘色 CTA。Stage badge、warning、success 用語意色（`--warning` 黃、`--success` 綠、`--danger` 紅），不要拿 accent 當裝飾。

### P4. Icon-first，不要 emoji
所有圖示走 `data-icon` SVG 系統。**禁止任何 emoji 出現在 UI 字串**（包含 topbar 按鈕、stage badge、toast）。emoji 的渲染依賴系統字體，會破壞深色介面的一致性。

### P5. 4-state 必備
任何 async 流程都要覆蓋 loading / empty / error / success。初次載入用 spinner、後續更新用 inline indicator、async 期間禁用按鈕。

### P6. 對比 ≥ AAA（文字 7:1）
`--text` / `--text-dim` / `--text-faint` 都在深色底達到 AAA。不要為了「柔和」把文字壓到 `#666`。如果一段文字看起來太搶，調的是字級或字重，不是亮度。

---

## 3. 七個 Anti-pattern（看到就要 refactor）

| # | Anti-pattern | 為什麼禁 |
|---|--------------|--------|
| A1 | 寫死 hex/rgb 顏色 | token 系統會被繞過，深色 / 未來主題切換會崩 |
| A2 | 寫死 px 間距（不在 4/8/12/16/24/32） | spacing scale 失效，整體節奏感跑掉 |
| A3 | emoji in UI 字串 | 跨系統字體渲染不一致，視覺崩 |
| A4 | 同畫面兩個以上橘色 CTA | accent 失去「這是主要動作」的訊號 |
| A5 | 開發狀態文字出現在使用者 UI | "Dev mode"、"WIP"、"TODO" 屬於 console 或 README，不是 UI |
| A6 | 自製 modal backdrop（`<div class="modal-backdrop">`） | 用原生 `<dialog>` + `::backdrop`，免費拿到 ESC、focus trap、tab 邊界 |
| A7 | 一行 11 個 topbar 按鈕 | 沒有分組就沒有層級。用 `.topbar-group` + 分隔線；同層級內最多 1 個 primary CTA |

---

## 4. Component Cheatsheet

> 完整實作在 `dashboard.css` / `app.css`。這裡只列規格，要實作直接抄既有 class。

### 按鈕 `.btn` + 變體
- `.btn`：base，邊框 + `--surface-raised` 底，hover 變 accent 邊框/文字
- `.btn-primary`：橘底，**整頁最多一個**
- `.btn-ghost`：透明底，邊框淡
- `.btn-icon`：32×32 方形，只放 icon
- 共通：`padding: 7px 12px`、`gap: 6px`、`border-radius: var(--radius-md)`、`font-size: 13px`、`font-weight: 500`
- focus：`outline: 2px solid var(--accent-ring)` + `outline-offset: 2px`
- disabled：`opacity: 0.45` + `cursor: not-allowed`

### Modal — 一律用原生 `<dialog>`
- `.modal` class 套在 `<dialog>` 上
- `::backdrop` 用 `--backdrop-strong` + `backdrop-filter: blur(6px)`
- 結構：`.modal-head`（標題 + 關閉 X）、`.modal-body`（內容）、`.modal-actions`（footer，貼底 `--surface-sunken`）
- 寬度：`min-width: 420px; max-width: 540px`

### Card（episode card）
- 底色 `--surface`，邊框 `--border`，hover 升起 `transform: translateY(-1px)` + 底色升到 `--surface-raised`
- 內距 `--space-lg`，圓角 `--radius-lg`
- 標題用 `--text`，hover 變 `--accent`；副資訊用 `--text-dim` + mono

### Status Pill（`.stage-badge`）
- **絕對不要 emoji**。用 `::before` 6×6 圓點 + 文字
- 配色用語意色 soft 變體（`--warning-soft` 等）+ 文字用對應實色

### Form Input
- 底色 `--surface-sunken`，邊框 `--border`，focus 邊框變 `--accent` + 3px ring
- 路徑、時碼、檔名一律 `font-family: var(--font-mono)`；自然語言用 `--font-sans`

### Loading / Empty / Error
- Spinner：14px，2px border，`border-top-color: var(--accent)`，0.8s linear infinite
- Empty：64px 圖示框（`--surface` 底 + `--border` 邊）+ 標題 + 提示
- Error：`--danger-soft` 底 + `--danger` 邊 + retry 按鈕

### Topbar
- Sticky，`backdrop-filter: blur(12px)`
- 控制項超過 3 個 → 用 `.topbar-group` 分組 + 分隔線；同 group 最多 1 個 primary

---

## 5. Tokens Index

完整定義見 `podcast_toolkit/web/static/tokens.css`。分類速查：

| 類別 | Tokens |
|------|--------|
| 間距 | `--space-xs/sm/md/lg/xl/2xl`（4/8/12/16/24/32） |
| 圓角 | `--radius-sm/md/lg/xl`（4/6/8/12） |
| 陰影 | `--shadow-sm/md/lg` |
| 背景 | `--bg` / `--surface` / `--surface-raised` / `--surface-sunken` |
| 邊框 | `--border` / `--border-strong` |
| 文字 | `--text` / `--text-dim` / `--text-faint` |
| Accent | `--accent` / `--accent-hover` / `--accent-soft` / `--accent-ring` |
| 語意 | `--danger(-soft)` / `--success(-soft)` / `--warning(-soft)` |
| Backdrop | `--backdrop-dim` / `--backdrop-strong` |
| 字體 | `--font-sans` / `--font-mono` |
| 字級 | `--text-xs/sm/base/md/lg/xl`（11/12/13/14/15/16） |
| 鏡頭語意 | `--cam-a` / `--cam-a-soft`（cam B 共用 `--accent`） |
| Icon | `--icon-sm/md/lg`（14/16/20） |
| 動效 | `--ease-out` / `--duration-fast`（120ms）/ `--duration-base`（180ms） |

---

## 6. 怎麼用這份文件

- **新功能 UI**：對著 §2 原則 + §4 cheatsheet 寫；遇到沒有的 component 就先寫 token-only 版本，不要寫死值
- **Code review**：拿 §3 anti-pattern 表掃一遍
- **design-shotgun**：自動讀這份檔產生 mockup 風格
- **不確定時**：抄既有 `dashboard.css`，那邊已經是規範範本
