// 鍵盤快捷鍵的單一資料來源（modal 總覽與時間工具列提示列共用）。
//
// 併軌理由：這份清單原本有兩個地方在管 —— index.html 的靜態 <dl class="shortcuts-list">
// 與 app.js 時間工具列的提示列字串 —— 而且已經漂移：E 梯新增的 [ ] 打點、以及
// 「⏱ 工具列開著時 P ＝切換循環」只進了提示列，modal 至今沒有。
// 新增／修改快捷鍵一律只動這張表，兩個消費端都從它渲染。
//
// 欄位：
//   keys      顯示成 <kbd> 的按鍵（陣列）；join 指定鍵之間的連接字（預設 " / "）
//   suffix    接在 kbd 後面的純文字（如「 + 滾輪」）
//   text      沒有按鍵的項目（如「點字幕時間」）——與 keys 二選一
//   desc      modal 的說明
//   inToolbar 是否出現在 ⏱ 時間工具列的提示列
//   hint      提示列用的短標籤（空間有限，不用 desc）
//   hintKeys  提示列的按鍵寫法，省略則沿用 keys（例：modal 寫「← / →」、提示列併成「←→」）

export const SHORTCUTS = [
  { keys: ["Space"], desc: "播放 / 暫停" },
  { keys: ["↑", "↓"], desc: "跳到上一張 / 下一張字幕卡" },
  { keys: ["P"], desc: "試聽目前所在字幕卡（播到卡尾自動停）" },
  { keys: ["J", "K"], desc: "跳下一張 / 上一張待複查卡" },
  { keys: ["Enter"], desc: "在游標處把字幕卡切成兩句" },
  { keys: ["Backspace"], desc: "子卡開頭按 → 合併回上一段" },
  { keys: ["⌘Z", "⌘⇧Z"], desc: "復原 / 重做" },
  { keys: ["Ctrl", "⌘"], join: "/", suffix: " + 滾輪", desc: "時間軸縮放（放大 / 縮小）" },
  { text: "點字幕時間", desc: "影片跳到該句開頭" },
  { text: "⏱ 調時間", desc: "字幕卡右側操作區；開啟時間微調工具列（Escape 關閉）" },

  // 以下六條只在 ⏱ 時間微調工具列開著時生效，同時也是提示列的內容。
  {
    keys: ["←", "→"],
    hintKeys: ["←→"],
    hint: "起點",
    inToolbar: true,
    desc: "⏱ 工具列開著時：起點 ±0.1s",
  },
  {
    keys: ["⌥←", "⌥→"],
    hintKeys: ["⌥←→"],
    hint: "訖點",
    inToolbar: true,
    desc: "⏱ 工具列開著時：訖點 ±0.1s",
  },
  {
    keys: ["⇧"],
    hintKeys: ["Shift"],
    hint: "×5",
    inToolbar: true,
    desc: "與 ←→／⌥←→ 併用：步長 ×5（0.5s）",
  },
  {
    keys: ["⌘"],
    hint: "×10",
    inToolbar: true,
    desc: "與 ←→／⌥←→ 併用：步長 ×10（1.0s）",
  },
  {
    keys: ["[", "]"],
    hint: "設為播放位置",
    inToolbar: true,
    desc: "⏱ 工具列開著時：把起點 / 訖點設為目前播放位置",
  },
  {
    keys: ["P"],
    hint: "循環",
    inToolbar: true,
    desc: "⏱ 工具列開著時：切換循環試聽（關閉工具列後 P 恢復單次試聽）",
  },

  { keys: ["?"], desc: "開啟這份快捷鍵清單" },
];

// modal 的 <dl> 內容。回傳是否成功渲染——失敗不靜默：找不到容器會寫 console.error，
// 資料表為空會在 modal 內留下可見的錯誤文字（而不是一個空白 modal）。
export function renderShortcutsModal(root) {
  const scope = root || document;
  const dl = scope.querySelector(".shortcuts-list");
  if (!dl) {
    console.error("[shortcuts] 找不到 .shortcuts-list，快捷鍵總覽沒有渲染");
    return false;
  }
  dl.textContent = "";
  if (!SHORTCUTS.length) {
    const dt = document.createElement("dt");
    dt.textContent = "⚠";
    const dd = document.createElement("dd");
    dd.className = "shortcuts-error";
    dd.textContent = "快捷鍵清單載入失敗（資料表是空的），請回報這個畫面。";
    dl.append(dt, dd);
    return false;
  }
  for (const s of SHORTCUTS) {
    const dt = document.createElement("dt");
    if (s.text) {
      dt.textContent = s.text;
    } else {
      const join = s.join || " / ";
      s.keys.forEach((k, i) => {
        if (i) dt.appendChild(document.createTextNode(join));
        const kbd = document.createElement("kbd");
        kbd.textContent = k;
        dt.appendChild(kbd);
      });
      if (s.suffix) dt.appendChild(document.createTextNode(s.suffix));
    }
    const dd = document.createElement("dd");
    dd.textContent = s.desc;
    dl.append(dt, dd);
  }
  return true;
}

// ⏱ 時間工具列底部那一行提示。回傳 HTML 字串（呼叫端用 innerHTML；
// kbd 內容全部來自本檔常數，沒有外部輸入）。
export function timeToolbarHintHtml() {
  const items = SHORTCUTS.filter((s) => s.inToolbar);
  if (!items.length) {
    return '<span class="te-hint-error">⚠ 快捷鍵提示載入失敗</span>';
  }
  return items
    .map((s) => {
      const keys = (s.hintKeys || s.keys).map((k) => `<kbd>${k}</kbd>`).join(" ");
      return `${keys} ${s.hint}`;
    })
    .join("｜");
}
