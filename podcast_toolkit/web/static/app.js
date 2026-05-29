// 編輯狀態：全部存在這裡，存檔時一次 POST。
const state = {
  name: "",
  crop: null,
  cropRatio: null, // "4:5" | "9:16" | "16:9" | null
  deletions: new Set(),
  cards: [],
  textOverrides: new Map(), // idx -> text
  typoDict: [], // [{wrong, right, note}]
  files: [], // [{path, size, transcribable, previewable}]
  previewPath: null, // null = main_video；否則為 ep.dir 內的相對路徑
  hasApiKey: false,
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
  const overlay = $("#caption-overlay");
  if (!c) {
    $("#crop-text").textContent = "裁切框：未設定（整張畫面）";
    $("#crop-frame").classList.add("hidden");
    // 字幕回到整個影片區（清除 inline style 讓 CSS 預設生效）
    overlay.style.left = "";
    overlay.style.right = "";
    overlay.style.bottom = "";
    overlay.style.fontSize = "";
    return;
  }
  const ratio = state.cropRatio ? `${state.cropRatio}` : "自訂";
  $("#crop-text").textContent =
    `裁切框：${ratio} · x=${(c.x * 100).toFixed(0)}% y=${(c.y * 100).toFixed(0)}%`;
  const frame = $("#crop-frame");
  frame.classList.remove("hidden");
  frame.style.left = `${c.x * 100}%`;
  frame.style.top = `${c.y * 100}%`;
  frame.style.width = `${c.width * 100}%`;
  frame.style.height = `${c.height * 100}%`;

  // 字幕鎖在裁切框內：左右各內縮 6% 裁切寬度、距框底 8% 裁切高度
  const padX = 0.06;
  const padBottom = 0.08;
  overlay.style.left = `${((c.x + c.width * padX) * 100).toFixed(2)}%`;
  overlay.style.right = `${((1 - c.x - c.width + c.width * padX) * 100).toFixed(2)}%`;
  overlay.style.bottom = `${((1 - c.y - c.height + c.height * padBottom) * 100).toFixed(2)}%`;
  // 字體依裁切寬度比例縮放，最小 11px 保持可讀
  const fontMax = Math.max(14, 22 * c.width);
  overlay.style.fontSize = `clamp(11px, ${(2.2 * c.width).toFixed(2)}vw, ${fontMax.toFixed(1)}px)`;
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

function renderCardSkeletons(n = 8) {
  const list = $("#cards-list");
  list.innerHTML = "";
  for (let i = 0; i < n; i++) {
    const sk = document.createElement("div");
    sk.className = "card-skeleton";
    sk.innerHTML = "<span></span><span></span><span></span>";
    list.appendChild(sk);
  }
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

async function loadEpisodeState() {
  // 只重抓 episode + cards，重新轉字幕後會用到
  const r = await fetch("/api/episode");
  if (!r.ok) throw new Error(`/api/episode HTTP ${r.status}`);
  const data = await r.json();
  state.name = data.name;
  state.crop = data.crop ?? { x: 0.05, y: 0.05, width: 0.9, height: 0.9 };
  state.deletions = new Set(data.deletions || []);
  state.cards = data.cards || [];
  state.textOverrides = new Map();
}

async function loadFiles() {
  try {
    const r = await fetch("/api/files");
    if (!r.ok) return;
    const data = await r.json();
    state.files = data.files || [];
  } catch (_) {}
}

async function loadConfig() {
  try {
    const r = await fetch("/api/config");
    if (!r.ok) return;
    const data = await r.json();
    state.hasApiKey = !!data.has_xai_api_key;
  } catch (_) {}
}

async function load() {
  const [, dictRes, ,] = await Promise.all([
    loadEpisodeState(),
    fetch("/api/typo-dict"),
    loadFiles(),
    loadConfig(),
  ]);
  state.typoDict = dictRes.ok ? await dictRes.json() : [];
  renderTopbar();
  renderCropInfo();
  renderCards();
  renderCaption();
  renderTypo();
  renderFiles();
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

// === Crop 框：固定比例 4:5 / 9:16 / 16:9，只能拖移不能 free resize ===
(function setupCrop() {
  const wrap = $(".video-wrap");
  const frame = $("#crop-frame");

  // 後端 crop 以 1920x1080（16:9）為基準算 px，預覽 .video-wrap 也是 16:9
  // 目標比例 t（寬/高）→ 標準化 cropW/cropH = t × 9/16
  const SOURCE_RATIO = 16 / 9;

  function clamp(v, lo, hi) {
    return Math.min(Math.max(v, lo), hi);
  }

  function cropForRatio(ratioStr) {
    const [rw, rh] = ratioStr.split(":").map(Number);
    const target = rw / rh;
    const wOverH = target / SOURCE_RATIO; // cropW / cropH（標準化）
    let width, height;
    if (wOverH <= 1) {
      height = 1.0;
      width = wOverH;
    } else {
      width = 1.0;
      height = 1.0 / wOverH;
    }
    return {
      x: (1 - width) / 2,
      y: (1 - height) / 2,
      width,
      height,
    };
  }

  function applyRatio(ratioStr) {
    state.crop = cropForRatio(ratioStr);
    state.cropRatio = ratioStr;
    renderCropInfo();
    updateRatioButtons();
  }

  function updateRatioButtons() {
    document.querySelectorAll(".ratio-btn").forEach((btn) => {
      btn.classList.toggle(
        "active",
        state.cropRatio === btn.dataset.ratio && state.crop != null,
      );
    });
  }

  // 拖移整框（位置變，大小不變）
  frame.addEventListener("mousedown", (e) => {
    if (!state.crop) return;
    if (e.target.classList.contains("handle")) return; // handle 自己處理
    e.preventDefault();
    const rect = wrap.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const c0 = { ...state.crop };

    function onMove(ev) {
      const dx = (ev.clientX - startX) / rect.width;
      const dy = (ev.clientY - startY) / rect.height;
      state.crop = {
        ...c0,
        x: clamp(c0.x + dx, 0, 1 - c0.width),
        y: clamp(c0.y + dy, 0, 1 - c0.height),
      };
      renderCropInfo();
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  // 四角縮放（鎖比例：固定 cropW/cropH 比，opposite corner 當錨點）
  function startResize(e, edge) {
    e.preventDefault();
    e.stopPropagation();
    if (!state.crop || !state.cropRatio) return;
    const rect = wrap.getBoundingClientRect();
    const c0 = { ...state.crop };
    // wOverH = cropW / cropH（標準化）；resize 過程不變
    const wOverH = c0.width / c0.height;

    // 錨點 = 對角的標準化座標
    const anchorX = edge.includes("l") ? c0.x + c0.width : c0.x;
    const anchorY = edge.includes("t") ? c0.y + c0.height : c0.y;
    const signX = edge.includes("l") ? -1 : 1; // 拖動方向：r=向右增寬, l=向左增寬
    const signY = edge.includes("t") ? -1 : 1;

    function onMove(ev) {
      const mx = (ev.clientX - rect.left) / rect.width;
      const my = (ev.clientY - rect.top) / rect.height;
      // 拖動點距離錨點的標準化長度（每個軸都取正值）
      let dw = Math.max(0.05, signX * (mx - anchorX));
      let dh = Math.max(0.05, signY * (my - anchorY));
      // 鎖比例：取兩軸中能容納的較大者，另一軸隨之
      let width, height;
      if (dw / dh > wOverH) {
        // 寬太大 → 以高為主
        height = dh;
        width = height * wOverH;
      } else {
        width = dw;
        height = width / wOverH;
      }
      // clamp 在 [0,1] 內，超出就回推
      const maxW = signX > 0 ? 1 - anchorX : anchorX;
      const maxH = signY > 0 ? 1 - anchorY : anchorY;
      if (width > maxW) {
        width = maxW;
        height = width / wOverH;
      }
      if (height > maxH) {
        height = maxH;
        width = height * wOverH;
      }
      const x = signX > 0 ? anchorX : anchorX - width;
      const y = signY > 0 ? anchorY : anchorY - height;
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

  document.querySelectorAll(".handle").forEach((h) => {
    h.addEventListener("mousedown", (e) => startResize(e, h.dataset.edge));
  });

  document.querySelectorAll(".ratio-btn").forEach((btn) => {
    btn.addEventListener("click", () => applyRatio(btn.dataset.ratio));
  });

  $("#crop-reset").addEventListener("click", () => {
    state.crop = null;
    state.cropRatio = null;
    renderCropInfo();
    updateRatioButtons();
  });

  // 載入後同步 active 狀態（如果 episode.yaml 已有 crop，預設不亮，使用者要重新選比例）
  updateRatioButtons();
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

// === 專案檔案 panel + 轉字幕 ===
function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function renderFiles() {
  const list = $("#files-list");
  const summary = $("#files-summary");
  list.innerHTML = "";
  const total = state.files.length;
  const audio = state.files.filter((f) => f.transcribable).length;
  const previewLabel = state.previewPath ? state.previewPath : "主影片";
  summary.textContent = `${total} 個檔案 · ${audio} 個可轉字幕 · 預覽中：${previewLabel}`;

  if (total === 0) {
    const empty = document.createElement("div");
    empty.className = "typo-empty";
    empty.textContent = "資料夾是空的";
    list.appendChild(empty);
    return;
  }

  for (const f of state.files) {
    const item = document.createElement("div");
    item.className = "file-item";
    const isActive = state.previewPath === f.path;
    if (isActive) item.classList.add("previewing");

    const path = document.createElement("div");
    path.className = "file-path";
    path.title = f.path;
    path.textContent = f.path;

    const size = document.createElement("div");
    size.className = "file-size";
    size.textContent = fmtSize(f.size);

    let preview;
    if (f.previewable) {
      preview = document.createElement("button");
      preview.className = "file-preview" + (isActive ? " active" : "");
      preview.textContent = isActive ? "📺 預覽中" : "📺 預覽";
      preview.title = "切換為此檔案預覽";
      preview.addEventListener("click", () => switchPreview(f.path));
    } else {
      preview = document.createElement("span");
      preview.className = "file-preview-placeholder";
      preview.textContent = "—";
    }

    let action;
    if (f.transcribable) {
      action = document.createElement("button");
      action.className = "file-stt";
      action.textContent = "🎙 轉字幕";
      action.title = state.hasApiKey
        ? "用 Grok STT 轉字幕並覆蓋 _v2.srt"
        : "請先設定 xAI API key（⚙）";
      action.addEventListener("click", () => requestTranscribe(f));
    } else {
      action = document.createElement("span");
      action.className = "file-stt-placeholder";
      action.textContent = "—";
    }

    item.append(path, size, preview, action);
    list.appendChild(item);
  }
}

function switchPreview(relPath) {
  const video = $("#video");
  // 同一個檔案再按一次 → 切回主影片
  if (state.previewPath === relPath) {
    state.previewPath = null;
    video.src = "/api/video";
  } else {
    state.previewPath = relPath;
    video.src = `/api/video?path=${encodeURIComponent(relPath)}`;
  }
  video.load();
  renderFiles();
}

// 簡易 modal 控制
function showModal(id) {
  $(`#${id}`).classList.remove("hidden");
}
function hideModal(id) {
  $(`#${id}`).classList.add("hidden");
}

// === 轉字幕流程 ===
function requestTranscribe(file) {
  if (!state.hasApiKey) {
    $("#transcribe-title").textContent = "尚未設定 API key";
    $("#transcribe-msg").innerHTML =
      "請先到右上角 ⚙ 設定 xAI API key，才能用 Grok STT 轉字幕。";
    const go = $("#transcribe-go");
    go.textContent = "去設定";
    go.disabled = false;
    go.onclick = () => {
      hideModal("transcribe-modal");
      openSettings();
    };
    $("#transcribe-cancel").onclick = () => hideModal("transcribe-modal");
    showModal("transcribe-modal");
    return;
  }

  $("#transcribe-title").textContent = "轉字幕確認";
  $("#transcribe-msg").innerHTML =
    `來源檔：<code>${file.path}</code><br>` +
    `大小：${fmtSize(file.size)}<br><br>` +
    `用 Grok STT（x.ai）轉字幕並覆寫 <code>_v2.srt</code>。<br>` +
    `預估時間：約音檔長度的 1 倍（3 分鐘片約 60–180 秒）。`;
  const go = $("#transcribe-go");
  go.textContent = "開始";
  go.disabled = false;
  go.onclick = () => runTranscribe(file);
  $("#transcribe-cancel").onclick = () => hideModal("transcribe-modal");
  showModal("transcribe-modal");
}

async function runTranscribe(file) {
  $("#transcribe-title").textContent = "轉字幕中…";
  $("#transcribe-msg").innerHTML =
    `處理中：<code>${file.path}</code><br>` +
    `會做：ffmpeg 壓縮 → 上傳 x.ai → 簡轉繁 → 寫 SRT<br>` +
    `<br><em>請保留這個分頁，不要關閉。</em>`;
  const go = $("#transcribe-go");
  const cancel = $("#transcribe-cancel");
  go.disabled = true;
  cancel.disabled = true;

  try {
    const r = await fetch("/api/transcribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: file.path }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
    const data = await r.json();

    $("#transcribe-title").textContent = "✅ 完成";
    $("#transcribe-msg").innerHTML =
      `已寫入：<code>${data.out_srt}</code><br>` + `正在重新載入編輯區…`;

    // 重抓 episode state 並重 render 字幕卡
    await loadEpisodeState();
    renderTopbar();
    renderCards();
    renderCaption();
    renderTypo();

    cancel.disabled = false;
    cancel.textContent = "關閉";
    cancel.onclick = () => {
      hideModal("transcribe-modal");
      cancel.textContent = "取消";
    };
  } catch (e) {
    $("#transcribe-title").textContent = "❌ 失敗";
    $("#transcribe-msg").innerHTML =
      `<div style="color:#ff6b35">${e.message}</div>`;
    cancel.disabled = false;
    cancel.textContent = "關閉";
    cancel.onclick = () => {
      hideModal("transcribe-modal");
      cancel.textContent = "取消";
    };
  }
}

// === 設定 modal ===
function openSettings() {
  const input = $("#settings-xai-key");
  input.value = "";
  input.type = "password";
  $("#settings-status").textContent = state.hasApiKey
    ? "已存在 API key（重新輸入會覆蓋；留空則維持原樣）"
    : "尚未設定";
  showModal("settings-modal");
}

$("#settings-btn").addEventListener("click", openSettings);

$("#settings-cancel").addEventListener("click", () =>
  hideModal("settings-modal"),
);

$("#settings-show").addEventListener("click", () => {
  const input = $("#settings-xai-key");
  input.type = input.type === "password" ? "text" : "password";
});

$("#settings-save").addEventListener("click", async () => {
  const input = $("#settings-xai-key");
  const key = input.value.trim();
  // 留空時不送 xai_api_key，保持原本設定
  const payload = key ? { xai_api_key: key } : {};
  const btn = $("#settings-save");
  btn.disabled = true;
  btn.textContent = "儲存中…";
  try {
    const r = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    state.hasApiKey = !!data.has_xai_api_key;
    hideModal("settings-modal");
    renderFiles();
  } catch (e) {
    alert(`儲存失敗：${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "儲存";
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

// === 換集 ===
function showSwitchError(msg) {
  const err = $("#ep-switch-error");
  err.textContent = msg;
  err.hidden = false;
}

function clearSwitchError() {
  const err = $("#ep-switch-error");
  err.textContent = "";
  err.hidden = true;
}

async function pickEpisodeFolder() {
  const btn = $("#ep-switch-btn");
  const dirty = state.deletions.size > 0 || state.textOverrides.size > 0;
  if (dirty && !confirm("有未儲存的修改，換集後會丟失，繼續？")) return;

  clearSwitchError();
  const origLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = "選擇中…";
  let picked = null;
  try {
    const r = await fetch("/api/episode/pick", { method: "POST" });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    if (data.cancelled || !data.path) {
      // 使用者取消，靜默結束
      return;
    }
    picked = data.path;
  } catch (e) {
    showSwitchError(`開啟資料夾失敗：${e.message}`);
    return;
  } finally {
    btn.disabled = false;
    btn.textContent = origLabel;
  }

  // 先 preview，沒有 episode.yaml 就跳 init modal
  let preview = null;
  try {
    const r = await fetch("/api/episode/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: picked }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
    preview = await r.json();
  } catch (e) {
    showSwitchError(`預覽資料夾失敗：${e.message}`);
    return;
  }

  if (preview.has_episode_yaml) {
    await switchEpisode(picked);
    return;
  }
  openInitModal(preview);
}

function openInitModal(preview) {
  $("#init-folder-path").textContent = preview.path;
  if (!preview.matches_convention) {
    $("#init-warn-block").hidden = false;
    $("#init-warn").textContent =
      `資料夾名「${preview.folder_name}」不符合 'YYYYMMDD 集名' 慣例，` +
      `episode.yaml 的 date / name 會留空、之後要手動填。`;
  } else {
    $("#init-warn-block").hidden = true;
  }
  const cur = $("#init-current-list");
  cur.innerHTML = "";
  if (preview.entries.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "（空資料夾）";
    cur.appendChild(empty);
  } else {
    for (const e of preview.entries) {
      const row = document.createElement("div");
      row.className = `row ${e.is_dir ? "dir" : ""}`;
      row.textContent = e.is_dir ? `📁 ${e.name}/` : `📄 ${e.name}`;
      cur.appendChild(row);
    }
  }
  const create = $("#init-create-list");
  create.innerHTML = "";
  for (const d of preview.subdirs_to_create) {
    const row = document.createElement("div");
    row.className = "row dir new";
    row.textContent = `📁 ${d}/`;
    create.appendChild(row);
  }
  for (const l of preview.asset_symlinks) {
    const row = document.createElement("div");
    row.className = "row new";
    row.textContent = `🔗 02_片頭片尾/${l}`;
    create.appendChild(row);
  }
  const yamlRow = document.createElement("div");
  yamlRow.className = "row new";
  yamlRow.textContent = "📄 episode.yaml";
  create.appendChild(yamlRow);
  const todoRow = document.createElement("div");
  todoRow.className = "row new";
  todoRow.textContent = "📄 TODO.md";
  create.appendChild(todoRow);

  const modal = $("#init-modal");
  modal.classList.remove("hidden");
  modal.dataset.path = preview.path;
}

function closeInitModal() {
  const modal = $("#init-modal");
  modal.classList.add("hidden");
  modal.dataset.path = "";
}

async function runInitAndSwitch() {
  const modal = $("#init-modal");
  const path = modal.dataset.path;
  if (!path) return;
  const goBtn = $("#init-go");
  const cancelBtn = $("#init-cancel");
  const orig = goBtn.textContent;
  goBtn.disabled = true;
  cancelBtn.disabled = true;
  goBtn.textContent = "建立中…";
  try {
    const r = await fetch("/api/episode/init", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
  } catch (e) {
    showSwitchError(`建立失敗：${e.message}`);
    goBtn.disabled = false;
    cancelBtn.disabled = false;
    goBtn.textContent = orig;
    return;
  }
  closeInitModal();
  goBtn.disabled = false;
  cancelBtn.disabled = false;
  goBtn.textContent = orig;
  await switchEpisode(path);
}

async function switchEpisode(newPath) {
  const btn = $("#ep-switch-btn");
  const origLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = "載入中…";
  renderCardSkeletons();
  try {
    const r = await fetch("/api/episode/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: newPath }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
    // 重設前端狀態
    state.previewPath = null;
    state.cropRatio = null;
    // 影片加 cache-bust 避免瀏覽器繼續用舊集的快取
    const video = $("#video");
    video.src = `/api/video?_=${Date.now()}`;
    video.load();
    // 重新拉所有狀態（episode/dict/files/config）
    await load();
  } catch (e) {
    showSwitchError(`換集失敗：${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = origLabel;
  }
}

$("#ep-switch-btn").addEventListener("click", pickEpisodeFolder);
$("#init-cancel").addEventListener("click", closeInitModal);
$("#init-go").addEventListener("click", runInitAndSwitch);
