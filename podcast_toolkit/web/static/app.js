// 編輯狀態：全部存在這裡，存檔時一次 POST。
const state = {
  name: "",
  crop: null,
  deletions: new Set(),
  cards: [],
  textOverrides: new Map(), // idx -> text
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
    });

    div.append(time, text, del);
    list.appendChild(div);
  }
}

async function load() {
  const res = await fetch("/api/episode");
  if (!res.ok) {
    alert("載入 episode 失敗");
    return;
  }
  const data = await res.json();
  state.name = data.name;
  state.crop = data.crop;
  state.deletions = new Set(data.deletions || []);
  state.cards = data.cards || [];
  renderTopbar();
  renderCropInfo();
  renderCards();
}

// 影片時間軸 → highlight 對應卡 + 自動 scroll
$("#video").addEventListener("timeupdate", () => {
  const t = $("#video").currentTime;
  const dur = $("#video").duration;
  $("#time").textContent = `${fmtTime(t)} / ${fmtTime(dur)}`;
  $("#seek").value = dur ? (t / dur) * 100 : 0;

  let active = null;
  for (const c of state.cards) {
    if (t >= c.start && t < c.end) {
      active = c.idx;
      break;
    }
  }
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
});

$("#play-btn").addEventListener("click", () => {
  const v = $("#video");
  if (v.paused) v.play();
  else v.pause();
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
