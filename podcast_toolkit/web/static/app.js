// 編輯狀態：全部存在這裡，存檔時一次 POST。
const state = {
  name: "",
  crop: null,
  deletions: new Set(),
  cards: [],
  textOverrides: new Map(), // idx -> text
  typoDict: [], // [{wrong, right, note}]
};

const $ = (sel) => document.querySelector(sel);

function fmtTime(sec) {
  if (!isFinite(sec)) return "00:00";
  const s = Math.floor(sec % 60)
    .toString()
    .padStart(2, "0");
  const m = Math.floor((sec / 60) % 60)
    .toString()
    .padStart(2, "0");
  return `${m}:${s}`;
}

function renderTopbar() {
  $("#title").textContent = state.name;
  const total = state.cards.length;
  const deleted = state.deletions.size;
  const dirty = state.textOverrides.size;
  $("#status").textContent =
    `字幕卡 ${total} 段 · 已刪 ${deleted} · 已修 ${dirty}`;
  const allDeleted = total > 0 && deleted === total;
  $("#save-btn").disabled = allDeleted;
}

function renderCropInfo() {
  const c = state.crop;
  if (!c) {
    $("#crop-text").textContent = "裁切框：未設定（整張畫面）";
    $("#crop-frame").classList.add("hidden");
    return;
  }
  $("#crop-text").textContent =
    `裁切框：x=${(c.x * 100).toFixed(0)}% y=${(c.y * 100).toFixed(0)}% ` +
    `w=${(c.width * 100).toFixed(0)}% h=${(c.height * 100).toFixed(0)}%`;
  const frame = $("#crop-frame");
  frame.classList.remove("hidden");
  frame.style.left = `${c.x * 100}%`;
  frame.style.top = `${c.y * 100}%`;
  frame.style.width = `${c.width * 100}%`;
  frame.style.height = `${c.height * 100}%`;
}

function activeCardAt(t) {
  for (const c of state.cards) {
    if (t >= c.start && t < c.end) return c;
  }
  return null;
}

function renderCaption() {
  const c = activeCardAt($("#video").currentTime);
  const overlay = $("#caption-overlay");
  if (!c || state.deletions.has(c.idx)) {
    overlay.textContent = "";
    return;
  }
  overlay.textContent = state.textOverrides.get(c.idx) ?? c.text;
}

function renderCards() {
  const list = $("#cards-list");
  list.innerHTML = "";
  for (const c of state.cards) {
    const div = document.createElement("div");
    div.className = "card";
    div.dataset.idx = c.idx;
    if (state.deletions.has(c.idx)) div.classList.add("deleted");

    const time = document.createElement("div");
    time.className = "card-time";
    time.textContent = `${fmtTime(c.start)}\n${fmtTime(c.end)}`;
    time.style.whiteSpace = "pre";
    time.addEventListener("click", () => {
      $("#video").currentTime = c.start;
    });

    const text = document.createElement("div");
    text.className = "card-text";
    text.contentEditable = "true";
    text.textContent = state.textOverrides.get(c.idx) ?? c.text;
    if (state.textOverrides.has(c.idx)) text.classList.add("dirty");
    text.addEventListener("blur", () => {
      const v = text.textContent.trim();
      const original = c.text;
      if (v && v !== original) {
        state.textOverrides.set(c.idx, v);
        text.classList.add("dirty");
      } else {
        state.textOverrides.delete(c.idx);
        text.classList.remove("dirty");
      }
      renderTopbar();
      renderCaption();
      renderTypo();
    });

    const del = document.createElement("button");
    del.className = "card-del";
    del.textContent = state.deletions.has(c.idx) ? "↺" : "✕";
    del.addEventListener("click", () => {
      if (state.deletions.has(c.idx)) {
        state.deletions.delete(c.idx);
      } else {
        state.deletions.add(c.idx);
      }
      renderCards();
      renderTopbar();
      renderCaption();
      renderTypo();
    });

    div.append(time, text, del);
    list.appendChild(div);
  }
}

async function load() {
  const [epRes, dictRes] = await Promise.all([
    fetch("/api/episode"),
    fetch("/api/typo-dict"),
  ]);
  if (!epRes.ok) {
    alert("載入 episode 失敗");
    return;
  }
  const data = await epRes.json();
  state.name = data.name;
  // 預設顯示裁切框：未設過時帶一個 90% 的初始框，讓使用者直接看到可拖動的範圍
  state.crop = data.crop ?? { x: 0.05, y: 0.05, width: 0.9, height: 0.9 };
  state.deletions = new Set(data.deletions || []);
  state.cards = data.cards || [];
  state.typoDict = dictRes.ok ? await dictRes.json() : [];
  renderTopbar();
  renderCropInfo();
  renderCards();
  renderCaption();
  renderTypo();
}

// === 錯字表 ===

// 取得卡片「當前文字」（含 textOverrides）並排除已刪除卡
function currentCardText(c) {
  if (state.deletions.has(c.idx)) return null;
  return state.textOverrides.get(c.idx) ?? c.text;
}

// 計算某字典項在本集卡片中的命中（return [{card, count}]）
function findHits(wrong) {
  const hits = [];
  if (!wrong) return hits;
  for (const c of state.cards) {
    const text = currentCardText(c);
    if (!text) continue;
    let count = 0;
    let i = 0;
    while ((i = text.indexOf(wrong, i)) !== -1) {
      count++;
      i += wrong.length;
    }
    if (count > 0) hits.push({ card: c, count });
  }
  return hits;
}

function applyDictEntry(wrong, right) {
  const hits = findHits(wrong);
  if (hits.length === 0) return 0;
  let total = 0;
  for (const { card } of hits) {
    const text = currentCardText(card);
    if (!text) continue;
    const replaced = text.split(wrong).join(right);
    if (replaced === card.text) {
      state.textOverrides.delete(card.idx);
    } else {
      state.textOverrides.set(card.idx, replaced);
    }
    total += 1;
  }
  return total;
}

async function saveDict() {
  const r = await fetch("/api/typo-dict", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entries: state.typoDict }),
  });
  if (!r.ok) {
    alert(`寫入字典失敗：HTTP ${r.status}`);
    return false;
  }
  state.typoDict = await r.json();
  return true;
}

function renderTypo() {
  // 全域字典區
  const dictList = $("#typo-dict-list");
  const dictSummary = $("#typo-dict-summary");
  dictList.innerHTML = "";
  let totalHitCards = 0;
  if (state.typoDict.length === 0) {
    const empty = document.createElement("div");
    empty.className = "typo-empty";
    empty.textContent = "字典是空的，先從「本集修改」加幾條進來";
    dictList.appendChild(empty);
  } else {
    for (const entry of state.typoDict) {
      const hits = findHits(entry.wrong);
      const totalCount = hits.reduce((s, h) => s + h.count, 0);
      if (totalCount > 0) totalHitCards += hits.length;

      const item = document.createElement("div");
      item.className = "typo-item " + (totalCount > 0 ? "hit" : "no-hit");

      const pair = document.createElement("div");
      pair.className = "typo-pair";
      pair.title = entry.note || "";
      pair.innerHTML = `<span class="wrong"></span><span class="arrow">→</span><span class="right"></span>`;
      pair.querySelector(".wrong").textContent = entry.wrong;
      pair.querySelector(".right").textContent = entry.right;
      pair.addEventListener("click", () => {
        if (hits.length > 0) {
          $("#video").currentTime = hits[0].card.start;
        }
      });

      const count = document.createElement("div");
      count.className = "typo-count";
      count.textContent = totalCount > 0 ? `${totalCount} 處` : "—";

      const apply = document.createElement("button");
      apply.className = "typo-apply";
      apply.textContent = "全部套用";
      apply.disabled = totalCount === 0;
      apply.addEventListener("click", () => {
        const n = applyDictEntry(entry.wrong, entry.right);
        if (n > 0) {
          renderCards();
          renderTopbar();
          renderCaption();
          renderTypo();
        }
      });

      item.append(pair, count, apply);
      dictList.appendChild(item);
    }
  }
  dictSummary.textContent = `${state.typoDict.length} 條 · 本集命中 ${totalHitCards} 卡`;

  // 本集修改區
  const overList = $("#typo-overrides-list");
  const overSummary = $("#typo-overrides-summary");
  overList.innerHTML = "";
  const overrides = [...state.textOverrides.entries()];
  overSummary.textContent = `${overrides.length} 處`;
  if (overrides.length === 0) {
    const empty = document.createElement("div");
    empty.className = "typo-empty";
    empty.textContent = "尚無本集修改";
    overList.appendChild(empty);
  } else {
    for (const [idx, newText] of overrides) {
      const card = state.cards.find((c) => c.idx === idx);
      if (!card) continue;
      const item = document.createElement("div");
      item.className = "typo-item";

      const pair = document.createElement("div");
      pair.className = "typo-pair";
      pair.title = `原：${card.text}\n改：${newText}`;
      pair.innerHTML = `<span class="wrong"></span><span class="arrow">→</span><span class="right"></span>`;
      pair.querySelector(".wrong").textContent = card.text;
      pair.querySelector(".right").textContent = newText;
      pair.addEventListener("click", () => {
        $("#video").currentTime = card.start;
      });

      const spacer = document.createElement("div");
      spacer.className = "typo-count";
      spacer.textContent = "";

      const addBtn = document.createElement("button");
      addBtn.className = "typo-apply";
      addBtn.textContent = "＋字典";
      addBtn.title = "把這條挑成 wrong/right 加入全域字典";
      addBtn.addEventListener("click", async () => {
        const wrong = prompt("要加入字典的「錯字」（substring）：", card.text);
        if (!wrong) return;
        const right = prompt("要替換成的「正字」：", newText);
        if (!right) return;
        // 去重：若已有同 wrong，覆寫
        const existing = state.typoDict.find((e) => e.wrong === wrong);
        if (existing) {
          existing.right = right;
        } else {
          state.typoDict.push({ wrong, right, note: "" });
        }
        if (await saveDict()) renderTypo();
      });

      item.append(pair, spacer, addBtn);
      overList.appendChild(item);
    }
  }
}

// 影片時間軸 → highlight 對應卡 + 自動 scroll + 字幕浮層
$("#video").addEventListener("timeupdate", () => {
  const t = $("#video").currentTime;
  const dur = $("#video").duration;
  $("#time").textContent = `${fmtTime(t)} / ${fmtTime(dur)}`;
  $("#seek").value = dur ? (t / dur) * 100 : 0;

  const activeCard = activeCardAt(t);
  const active = activeCard ? activeCard.idx : null;
  document
    .querySelectorAll(".card.playing")
    .forEach((el) => el.classList.remove("playing"));
  if (active != null) {
    const el = document.querySelector(`.card[data-idx="${active}"]`);
    if (el) {
      el.classList.add("playing");
      el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }
  renderCaption();
});

const playBtn = $("#play-btn");
playBtn.addEventListener("click", () => {
  const v = $("#video");
  if (v.paused) v.play();
  else v.pause();
});
// 由影片事件統一更新圖示，避免 click handler 與程式化 play/pause 不同步
$("#video").addEventListener("play", () => {
  playBtn.textContent = "⏸";
});
$("#video").addEventListener("pause", () => {
  playBtn.textContent = "▶";
});

$("#seek").addEventListener("input", (e) => {
  const v = $("#video");
  if (v.duration) v.currentTime = (e.target.value / 100) * v.duration;
});

load();

// === Crop 框互動 ===
(function setupCrop() {
  const wrap = $(".video-wrap");
  const frame = $("#crop-frame");

  function clamp(v, lo, hi) {
    return Math.min(Math.max(v, lo), hi);
  }

  function ensureCrop() {
    if (!state.crop) {
      state.crop = { x: 0.05, y: 0.05, width: 0.9, height: 0.9 };
      renderCropInfo();
    }
  }

  function startDrag(e, mode, edge) {
    e.preventDefault();
    e.stopPropagation();
    ensureCrop();
    const rect = wrap.getBoundingClientRect();
    const startX = e.clientX,
      startY = e.clientY;
    const c0 = { ...state.crop };

    function onMove(ev) {
      const dx = (ev.clientX - startX) / rect.width;
      const dy = (ev.clientY - startY) / rect.height;
      let { x, y, width, height } = c0;

      if (mode === "move") {
        x = clamp(c0.x + dx, 0, 1 - c0.width);
        y = clamp(c0.y + dy, 0, 1 - c0.height);
      } else {
        if (edge.includes("l")) {
          const nx = clamp(c0.x + dx, 0, c0.x + c0.width - 0.05);
          width = c0.x + c0.width - nx;
          x = nx;
        }
        if (edge.includes("r")) {
          width = clamp(c0.width + dx, 0.05, 1 - c0.x);
        }
        if (edge.includes("t")) {
          const ny = clamp(c0.y + dy, 0, c0.y + c0.height - 0.05);
          height = c0.y + c0.height - ny;
          y = ny;
        }
        if (edge.includes("b")) {
          height = clamp(c0.height + dy, 0.05, 1 - c0.y);
        }
      }
      state.crop = { x, y, width, height };
      renderCropInfo();
    }

    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  frame.addEventListener("mousedown", (e) => {
    if (e.target.classList.contains("handle")) return;
    startDrag(e, "move", null);
  });
  document.querySelectorAll(".handle").forEach((h) => {
    h.addEventListener("mousedown", (e) =>
      startDrag(e, "resize", h.dataset.edge),
    );
  });

  $("#crop-reset").addEventListener("click", () => {
    state.crop = null;
    renderCropInfo();
  });

  // 影片若整張未設過 crop，第一次點影片區自動建一個預設框
  wrap.addEventListener("dblclick", () => ensureCrop());
})();

// === 儲存 / 取消 ===
$("#save-btn").addEventListener("click", async () => {
  $("#save-btn").disabled = true;
  $("#save-btn").textContent = "儲存中…";
  const payload = {
    crop: state.crop,
    deletions: [...state.deletions].sort((a, b) => a - b),
    cards: [...state.textOverrides.entries()].map(([idx, text]) => ({
      idx,
      text,
    })),
  };
  try {
    const r = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    document.body.innerHTML =
      "<div style='padding:40px;text-align:center;font-size:16px'>" +
      "✅ 已儲存，可以關閉這個分頁。" +
      "</div>";
  } catch (e) {
    alert(`儲存失敗：${e.message}`);
    $("#save-btn").disabled = false;
    $("#save-btn").textContent = "完成並儲存";
  }
});

$("#cancel-btn").addEventListener("click", async () => {
  const dirty =
    state.deletions.size > 0 ||
    state.textOverrides.size > 0 ||
    state.crop != null;
  if (dirty && !confirm("未儲存的修改會丟失，確定取消？")) return;
  try {
    await fetch("/api/shutdown", { method: "POST" });
  } catch (_) {}
  document.body.innerHTML =
    "<div style='padding:40px;text-align:center;font-size:16px'>" +
    "已取消，可以關閉這個分頁。" +
    "</div>";
});
