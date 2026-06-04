// 編輯狀態：全部存在這裡，存檔時一次 POST。
const state = {
  name: "",
  activeVersion: "yt", // "yt" | "reels"
  cropYt: null,
  cropReels: null,
  cropRatioYt: null, // "4:5" | "9:16" | "16:9" | null
  cropRatioReels: null,
  deletions: new Set(),
  susChecked: new Set(), // 紅卡批次刪除的 checkbox 勾選集合（card.idx）
  cards: [],
  textOverrides: new Map(), // idx -> text
  typoDict: [], // [{wrong, right, note}]
  files: [], // [{path, size, transcribable, previewable}]
  previewPath: null, // null = main_video；否則為 ep.dir 內的相對路徑
  hasApiKey: false,
  hasGeminiKey: false,
  sttProvider: "xai", // "xai" | "gemini"
  needsTranscribe: false, // true 代表這集還沒跑過 transcribe/resegment，沒 _v2.srt
  headTrimSec: 0, // 影片開頭要砍掉幾秒
  tailTrimSec: 0, // 影片結尾要砍掉幾秒
  // 雙鏡頭：cameras = {a, b?}（僅雙機集才有 b），用來判斷 UI 是否要顯示 A/B toggle
  cameras: {},
  // 字幕卡 idx -> "a" | "b"，只記 explicit 標過的；其他卡靠 carry-forward 推算
  camerasMapping: new Map(),
  // T23a-followup：cam B UI 用，避免使用者手改 yaml
  camBCandidates: [], // 後端掃 01_母帶/*.mp4 排除 cam A
  camSyncOffsetB: 0, // 秒；cam B 相對 cam A 的對齊偏移
  // Undo / Redo：只追蹤會進 episode.yaml 的編輯狀態
  // 換集 / 儲存成功 → 一律清空兩 stacks（與 dirty 概念對齊）
  undoStack: [],
  redoStack: [],
};

const UNDO_MAX = 100;

function getActiveCrop() {
  return state.activeVersion === "yt" ? state.cropYt : state.cropReels;
}
function setActiveCrop(crop) {
  if (state.activeVersion === "yt") {
    state.cropYt = crop;
  } else {
    state.cropReels = crop;
  }
}
function getActiveCropRatio() {
  return state.activeVersion === "yt"
    ? state.cropRatioYt
    : state.cropRatioReels;
}
function setActiveCropRatio(ratio) {
  if (state.activeVersion === "yt") {
    state.cropRatioYt = ratio;
  } else {
    state.cropRatioReels = ratio;
  }
}

// === Undo / Redo（in-memory 編輯） ===
// 追：deletions / textOverrides / cropYt / cropReels / cropRatio* / camerasMapping / head|tailTrimSec
// 不追：susChecked（UI 暫態）、typoDict（自己的 API）、播放位置
// mutation 前呼 pushUndo() → snapshot 入 undoStack；Cmd+Z undo、Cmd+Shift+Z redo
function snapshotEditState() {
  return {
    deletions: new Set(state.deletions),
    textOverrides: new Map(state.textOverrides),
    cropYt: state.cropYt ? { ...state.cropYt } : null,
    cropReels: state.cropReels ? { ...state.cropReels } : null,
    cropRatioYt: state.cropRatioYt,
    cropRatioReels: state.cropRatioReels,
    camerasMapping: new Map(state.camerasMapping),
    headTrimSec: state.headTrimSec,
    tailTrimSec: state.tailTrimSec,
  };
}

function applyEditSnapshot(snap) {
  state.deletions = new Set(snap.deletions);
  state.textOverrides = new Map(snap.textOverrides);
  state.cropYt = snap.cropYt ? { ...snap.cropYt } : null;
  state.cropReels = snap.cropReels ? { ...snap.cropReels } : null;
  state.cropRatioYt = snap.cropRatioYt;
  state.cropRatioReels = snap.cropRatioReels;
  state.camerasMapping = new Map(snap.camerasMapping);
  state.headTrimSec = snap.headTrimSec;
  state.tailTrimSec = snap.tailTrimSec;
}

function pushUndo() {
  state.undoStack.push(snapshotEditState());
  if (state.undoStack.length > UNDO_MAX) state.undoStack.shift();
  state.redoStack = [];
}

function clearUndoStacks() {
  state.undoStack = [];
  state.redoStack = [];
}

function rerenderEditState() {
  renderTopbar();
  renderCards();
  renderCaption();
  renderTypo();
  renderCropInfo();
  renderTrimControls();
  // crop ratio 按鈕在 setupCrop IIFE 內，沒 export → 這裡重算
  document.querySelectorAll(".ratio-btn").forEach((btn) => {
    btn.classList.toggle(
      "active",
      getActiveCropRatio() === btn.dataset.ratio && getActiveCrop() != null,
    );
  });
}

function undo() {
  if (state.undoStack.length === 0) return;
  state.redoStack.push(snapshotEditState());
  applyEditSnapshot(state.undoStack.pop());
  rerenderEditState();
}

function redo() {
  if (state.redoStack.length === 0) return;
  state.undoStack.push(snapshotEditState());
  applyEditSnapshot(state.redoStack.pop());
  rerenderEditState();
}

document.addEventListener("keydown", (e) => {
  const ctrl = e.metaKey || e.ctrlKey;
  if (!ctrl) return;
  const t = e.target;
  // input/textarea/contenteditable focused → 讓瀏覽器原生 Cmd+Z 處理（編輯文字內容）
  if (
    t &&
    (t.tagName === "INPUT" ||
      t.tagName === "TEXTAREA" ||
      t.isContentEditable === true)
  ) {
    return;
  }
  const key = (e.key || "").toLowerCase();
  if (key !== "z") return;
  if (e.shiftKey) {
    e.preventDefault();
    redo();
  } else {
    e.preventDefault();
    undo();
  }
});

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
  if (state.needsTranscribe) {
    $("#status").textContent = "尚未轉字幕（從左側檔案列點 🎙 開始）";
    $("#save-btn").disabled = true;
    return;
  }
  const total = state.cards.length;
  const deleted = state.deletions.size;
  const dirty = state.textOverrides.size;
  const head = state.headTrimSec || 0;
  const tail = state.tailTrimSec || 0;
  let line = `字幕卡 ${total} 段 · 已刪 ${deleted} · 已修 ${dirty}`;
  if (head > 0 || tail > 0) {
    line += ` · 頭 ${head.toFixed(1)}s / 尾 ${tail.toFixed(1)}s`;
  }
  $("#status").textContent = line;
  const allDeleted = total > 0 && deleted === total;
  $("#save-btn").disabled = allDeleted;
}

function renderTrimControls() {
  const head = state.headTrimSec || 0;
  const tail = state.tailTrimSec || 0;
  $("#trim-head-val").textContent = `${head.toFixed(1)}s`;
  $("#trim-tail-val").textContent = `${tail.toFixed(1)}s`;
  $("#trim-head-btn").classList.toggle("active", head > 0);
  $("#trim-tail-btn").classList.toggle("active", tail > 0);

  const dur = $("#video").duration || 0;
  const headBand = $("#trim-band-head");
  const tailBand = $("#trim-band-tail");
  if (dur > 0 && head > 0) {
    headBand.style.width = `${Math.min(100, (head / dur) * 100).toFixed(2)}%`;
    headBand.style.display = "block";
  } else {
    headBand.style.display = "none";
  }
  if (dur > 0 && tail > 0) {
    tailBand.style.width = `${Math.min(100, (tail / dur) * 100).toFixed(2)}%`;
    tailBand.style.display = "block";
  } else {
    tailBand.style.display = "none";
  }

  const hint = $("#trim-hint");
  if (head > 0 || tail > 0) {
    const remain = Math.max(0, dur - head - tail);
    hint.textContent = `保留 ${remain.toFixed(1)}s / 總長 ${dur.toFixed(1)}s`;
  } else {
    hint.textContent = "把播放游標停在要切的位置再按設頭 / 設尾";
  }
}

function renderCropInfo() {
  const c = getActiveCrop();
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
  const ratio = getActiveCropRatio() ? `${getActiveCropRatio()}` : "自訂";
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

// 算這張卡實際生效的鏡頭：往前找最近一張 explicit 標過的卡，沒有就回 "a"
// 注意：carry-forward 是依 state.cards 的順序，不是 idx 大小（idx 不一定連續）
function computeEffectiveCamera(idx) {
  const pos = state.cards.findIndex((c) => c.idx === idx);
  if (pos < 0) return "a";
  for (let i = pos; i >= 0; i--) {
    const v = state.camerasMapping.get(state.cards[i].idx);
    if (v === "a" || v === "b") return v;
  }
  return "a";
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
  if (state.needsTranscribe) {
    const empty = document.createElement("div");
    empty.className = "typo-empty";
    empty.style.padding = "24px 12px";
    empty.style.lineHeight = "1.6";
    empty.innerHTML =
      '<div style="font-size:14px;margin-bottom:8px">這一集還沒轉字幕</div>' +
      '<div style="color:#888;font-size:12px">到左側「檔案」面板找一軌主檔（通常是 Mic / Stereo Mix），點 🎙 開始轉字幕。轉完會自動回到這裡。</div>';
    list.appendChild(empty);
    return;
  }
  const hasCamB = !!state.cameras && !!state.cameras.b;
  for (const c of state.cards) {
    const div = document.createElement("div");
    div.className = "card";
    div.dataset.idx = c.idx;
    if (state.deletions.has(c.idx)) div.classList.add("deleted");
    if (c.suspicious_pause) div.classList.add("suspicious");
    // 雙機集：標記實際生效鏡頭，CSS 用 .card.cam-b 染左邊框
    if (hasCamB) {
      const eff = computeEffectiveCamera(c.idx);
      div.classList.add(eff === "b" ? "cam-b" : "cam-a");
      div.classList.add("card-has-cam");
    }

    const susBox = document.createElement("input");
    susBox.type = "checkbox";
    susBox.className = "card-sus-check";
    if (!c.suspicious_pause) susBox.classList.add("hidden");
    susBox.checked = state.susChecked.has(c.idx);
    susBox.title = c.suspicious_pause
      ? `可疑原因：${(c.suspicious_reasons || []).join(", ")}`
      : "";
    susBox.addEventListener("click", (e) => e.stopPropagation());
    susBox.addEventListener("change", () => {
      if (susBox.checked) {
        state.susChecked.add(c.idx);
      } else {
        state.susChecked.delete(c.idx);
      }
      renderSusToolbar();
    });

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
      const willSet = !!v && v !== original;
      const nextValue = willSet ? v : null;
      const currentValue = state.textOverrides.has(c.idx)
        ? state.textOverrides.get(c.idx)
        : null;
      // 沒實際改變 → 不入 undo stack，避免每次 focus/blur 都污染歷史
      if (nextValue === currentValue) {
        return;
      }
      pushUndo();
      if (willSet) {
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
    // Enter = 提交 + 跳下一卡編輯；Shift+Enter 保留原生換行 escape hatch
    // 注意 IME 組字中（如注音、拼音選字）不能攔 Enter，會吃掉候選確認
    text.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
      e.preventDefault();
      text.blur();
      const cards = Array.from(document.querySelectorAll("#cards-list .card"));
      const here = cards.indexOf(div);
      for (let i = here + 1; i < cards.length; i++) {
        const next = cards[i].querySelector(".card-text");
        if (next) {
          next.focus();
          break;
        }
      }
    });

    const del = document.createElement("button");
    del.className = "card-del";
    del.textContent = state.deletions.has(c.idx) ? "↺" : "✕";
    del.addEventListener("click", () => {
      pushUndo();
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

    // 雙機集才有 A/B 膠囊；已刪除卡淡化但保留位置避免格線跳
    let camPill = null;
    if (hasCamB) {
      const eff = computeEffectiveCamera(c.idx);
      camPill = document.createElement("div");
      camPill.className = "card-cam";
      if (state.deletions.has(c.idx)) camPill.classList.add("muted");

      const aBtn = document.createElement("button");
      aBtn.type = "button";
      aBtn.className = "cam-btn cam-a-btn" + (eff === "a" ? " active" : "");
      aBtn.textContent = "A";
      aBtn.title = state.camerasMapping.get(c.idx)
        ? "目前鏡頭（已 explicit 標記）"
        : "目前鏡頭（沿用前一張）";
      aBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        // 已經 explicit 標 a → 不入 stack 也不重畫
        if (state.camerasMapping.get(c.idx) === "a") return;
        pushUndo();
        state.camerasMapping.set(c.idx, "a");
        renderCards();
      });

      const bBtn = document.createElement("button");
      bBtn.type = "button";
      bBtn.className = "cam-btn cam-b-btn" + (eff === "b" ? " active" : "");
      bBtn.textContent = "B";
      bBtn.title = state.camerasMapping.get(c.idx)
        ? "切到 B 鏡頭（已 explicit 標記）"
        : "切到 B 鏡頭";
      bBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (state.camerasMapping.get(c.idx) === "b") return;
        pushUndo();
        state.camerasMapping.set(c.idx, "b");
        renderCards();
      });

      camPill.append(aBtn, bBtn);
    }

    if (camPill) {
      div.append(susBox, time, text, camPill, del);
    } else {
      div.append(susBox, time, text, del);
    }
    list.appendChild(div);
  }
  renderSusToolbar();
}

// 紅卡 toolbar：總可疑數 / 已勾數 / 全選 / 刪除已勾
function renderSusToolbar() {
  const bar = $("#sus-toolbar");
  // 還沒刪除的可疑卡才算數
  const susCards = state.cards.filter(
    (c) => c.suspicious_pause && !state.deletions.has(c.idx),
  );
  if (susCards.length === 0) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  $("#sus-count").textContent = susCards.length;

  // susChecked 內可能有已被刪除或不再可疑的 idx，清掉
  const validIds = new Set(susCards.map((c) => c.idx));
  for (const idx of [...state.susChecked]) {
    if (!validIds.has(idx)) state.susChecked.delete(idx);
  }
  const checkedCount = state.susChecked.size;
  $("#sus-checked-count").textContent = `已勾 ${checkedCount}`;
  $("#sus-delete-checked").disabled = checkedCount === 0;

  // 全選按鈕：全勾就顯示「☐ 取消全選」反之顯示「☐ 全選紅卡」
  const allChecked = susCards.length > 0 && checkedCount === susCards.length;
  $("#sus-select-all").textContent = allChecked ? "☑ 取消全選" : "☐ 全選紅卡";
}

async function loadEpisodeState() {
  // 只重抓 episode + cards，重新轉字幕後會用到
  const r = await fetch("/api/episode");
  if (!r.ok) throw new Error(`/api/episode HTTP ${r.status}`);
  const data = await r.json();
  state.name = data.name;
  state.cropYt = data.crop_yt || null;
  state.cropReels = data.crop_reels || null;
  state.deletions = new Set(data.deletions || []);
  state.cards = data.cards || [];
  state.textOverrides = new Map();
  state.susChecked = new Set();
  state.needsTranscribe = !!data.needs_transcribe;
  state.headTrimSec = Number(data.head_trim_sec) || 0;
  state.tailTrimSec = Number(data.tail_trim_sec) || 0;
  // 雙鏡頭 mapping：API 回傳 key 是字串（JSON 不支援 int key），這裡轉回 Number
  state.cameras = data.cameras || {};
  state.camerasMapping = new Map(
    Object.entries(data.cameras_mapping || {})
      .map(([k, v]) => [Number(k), v])
      .filter(([_, v]) => v === "a" || v === "b"),
  );
  // T23a-followup：cam B 候選 + 同步 offset（給 modal 用）
  state.camBCandidates = Array.isArray(data.cam_b_candidates)
    ? data.cam_b_candidates
    : [];
  state.camSyncOffsetB = Number((data.camera_sync_offset || {}).b || 0);
  // 換集 / 重抓 episode → 既有的 undo 紀錄不再有意義（idx 範圍可能不同）
  clearUndoStacks();
}

function setupSusToolbar() {
  $("#sus-select-all").addEventListener("click", () => {
    const susCards = state.cards.filter(
      (c) => c.suspicious_pause && !state.deletions.has(c.idx),
    );
    const allChecked =
      susCards.length > 0 && state.susChecked.size === susCards.length;
    if (allChecked) {
      state.susChecked.clear();
    } else {
      state.susChecked = new Set(susCards.map((c) => c.idx));
    }
    renderCards();
    renderTopbar();
    renderCaption();
  });

  $("#sus-delete-checked").addEventListener("click", () => {
    if (state.susChecked.size === 0) return;
    pushUndo();
    for (const idx of state.susChecked) state.deletions.add(idx);
    state.susChecked.clear();
    renderCards();
    renderTopbar();
    renderCaption();
    renderTypo();
  });
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
    state.hasGeminiKey = !!data.has_gemini_api_key;
    state.sttProvider = data.provider || "xai";
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
  renderTrimControls();
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
        // 先確認會命中再 snapshot，避免空套用污染 undo 歷史
        if (findHits(entry.wrong).length === 0) return;
        pushUndo();
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
// C3：播放時若 currentTime 進入 tail trim 區 → 自動暫停（拖 seek 越界不阻擋）
function autoPauseAtTailTrim() {
  const v = $("#video");
  const dur = v.duration || 0;
  const tail = state.tailTrimSec || 0;
  if (dur <= 0 || tail <= 0 || v.paused) return;
  const limit = dur - tail;
  if (v.currentTime >= limit) {
    v.pause();
    v.currentTime = Math.max(0, limit);
  }
}

$("#video").addEventListener("timeupdate", () => {
  autoPauseAtTailTrim();
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
  // C3：按 play 時若卡在 head trim 區 → 自動跳到 headTrim 邊界
  const v = $("#video");
  const head = state.headTrimSec || 0;
  if (head > 0 && v.currentTime < head) v.currentTime = head;
});
$("#video").addEventListener("pause", () => {
  playBtn.textContent = "▶";
});

$("#seek").addEventListener("input", (e) => {
  const v = $("#video");
  if (v.duration) v.currentTime = (e.target.value / 100) * v.duration;
});

// 影片載入完才能算頭尾 trim 在 seek 上的百分比，所以這裡也要重畫
$("#video").addEventListener("loadedmetadata", () => {
  renderTrimControls();
});

// 頭尾 trim 按鈕：用目前播放位置設值，再次按同位置 → 視為清除
$("#trim-head-btn").addEventListener("click", () => {
  const v = $("#video");
  const dur = v.duration || 0;
  if (!dur) return;
  const t = Math.max(
    0,
    Math.min(v.currentTime, dur - (state.tailTrimSec || 0)),
  );
  const next = Math.round(t * 10) / 10;
  // 在同一位置再按一次 → 取消
  const nextValue = Math.abs(next - state.headTrimSec) < 0.05 ? 0 : next;
  if (nextValue === state.headTrimSec) return;
  pushUndo();
  state.headTrimSec = nextValue;
  renderTrimControls();
  renderTopbar();
});

$("#trim-tail-btn").addEventListener("click", () => {
  const v = $("#video");
  const dur = v.duration || 0;
  if (!dur) return;
  const tailFromEnd = Math.max(
    0,
    Math.min(dur - v.currentTime, dur - (state.headTrimSec || 0)),
  );
  const next = Math.round(tailFromEnd * 10) / 10;
  const nextValue = Math.abs(next - state.tailTrimSec) < 0.05 ? 0 : next;
  if (nextValue === state.tailTrimSec) return;
  pushUndo();
  state.tailTrimSec = nextValue;
  renderTrimControls();
  renderTopbar();
});

$("#trim-reset").addEventListener("click", () => {
  if (state.headTrimSec === 0 && state.tailTrimSec === 0) return;
  pushUndo();
  state.headTrimSec = 0;
  state.tailTrimSec = 0;
  renderTrimControls();
  renderTopbar();
});

// C5：智慧建議 — POST /api/detect-silence；結果顯示在 hint，按下 hint 套用
$("#trim-suggest-btn").addEventListener("click", async () => {
  const btn = $("#trim-suggest-btn");
  const hint = $("#trim-suggest-hint");
  btn.disabled = true;
  hint.textContent = "分析中…";
  hint.classList.remove("error");
  try {
    const r = await fetch("/api/detect-silence", { method: "POST" });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
    const { head_silence_sec } = await r.json();
    if (head_silence_sec <= 0) {
      hint.textContent = "開頭沒有可裁切的靜音";
      return;
    }
    const seconds = Math.round(head_silence_sec * 10) / 10;
    hint.innerHTML = `建議裁 <strong>${seconds.toFixed(1)}s</strong>`;
    const apply = document.createElement("button");
    apply.textContent = "套用";
    apply.type = "button";
    apply.className = "trim-suggest-apply";
    apply.addEventListener("click", () => {
      if (seconds === state.headTrimSec) {
        hint.textContent = `已套用 ${seconds.toFixed(1)}s`;
        return;
      }
      pushUndo();
      state.headTrimSec = seconds;
      renderTrimControls();
      renderTopbar();
      hint.textContent = `已套用 ${seconds.toFixed(1)}s`;
    });
    hint.append(" ", apply);
  } catch (e) {
    hint.textContent = `失敗：${e.message}`;
    hint.classList.add("error");
  } finally {
    btn.disabled = false;
  }
});

load().catch((err) => {
  $("#title").textContent = "載入失敗";
  $("#status").textContent = `載入失敗：${err?.message || err}`;
  console.error(err);
});

initUploadDropZone();

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
    // 同比例再按一次（且已 active）→ 視為 no-op，避免污染 undo 歷史
    if (getActiveCropRatio() === ratioStr && getActiveCrop() != null) {
      return;
    }
    pushUndo();
    setActiveCrop(cropForRatio(ratioStr));
    setActiveCropRatio(ratioStr);
    renderCropInfo();
    updateRatioButtons();
  }

  function updateRatioButtons() {
    document.querySelectorAll(".ratio-btn").forEach((btn) => {
      btn.classList.toggle(
        "active",
        getActiveCropRatio() === btn.dataset.ratio && getActiveCrop() != null,
      );
    });
  }

  // 拖移整框（位置變，大小不變）
  frame.addEventListener("mousedown", (e) => {
    if (!getActiveCrop()) return;
    if (e.target.classList.contains("handle")) return; // handle 自己處理
    e.preventDefault();
    const rect = wrap.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const c0 = { ...getActiveCrop() };
    // 第一次 onMove 才 push — 純按一下沒拖動不算編輯
    let pushed = false;

    function onMove(ev) {
      if (!pushed) {
        pushUndo();
        pushed = true;
      }
      const dx = (ev.clientX - startX) / rect.width;
      const dy = (ev.clientY - startY) / rect.height;
      setActiveCrop({
        ...c0,
        x: clamp(c0.x + dx, 0, 1 - c0.width),
        y: clamp(c0.y + dy, 0, 1 - c0.height),
      });
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
    if (!getActiveCrop()) return;
    const rect = wrap.getBoundingClientRect();
    const c0 = { ...getActiveCrop() };
    // wOverH = cropW / cropH（標準化）；resize 過程不變
    const wOverH = c0.width / c0.height;

    // 錨點 = 對角的標準化座標
    const anchorX = edge.includes("l") ? c0.x + c0.width : c0.x;
    const anchorY = edge.includes("t") ? c0.y + c0.height : c0.y;
    const signX = edge.includes("l") ? -1 : 1; // 拖動方向：r=向右增寬, l=向左增寬
    const signY = edge.includes("t") ? -1 : 1;
    let pushed = false;

    function onMove(ev) {
      if (!pushed) {
        pushUndo();
        pushed = true;
      }
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
      setActiveCrop({ x, y, width, height });
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
    if (getActiveCrop() == null && getActiveCropRatio() == null) return;
    pushUndo();
    setActiveCrop(null);
    setActiveCropRatio(null);
    renderCropInfo();
    updateRatioButtons();
  });

  // 載入後同步 active 狀態（如果 episode.yaml 已有 crop，預設不亮，使用者要重新選比例）
  updateRatioButtons();
})();

function setupVersionTabs() {
  document.querySelectorAll(".version-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const v = btn.dataset.version;
      if (v === state.activeVersion) return;
      state.activeVersion = v;
      document.querySelectorAll(".version-tab").forEach((b) => {
        b.classList.toggle("active", b.dataset.version === v);
      });
      renderCropInfo();
      // 同步 ratio 按鈕到 active 版本的狀態
      document.querySelectorAll(".ratio-btn").forEach((b) => {
        b.classList.toggle(
          "active",
          getActiveCropRatio() === b.dataset.ratio && getActiveCrop() != null,
        );
      });
    });
  });
}

// === 儲存 / 取消 ===
$("#save-btn").addEventListener("click", async () => {
  $("#save-btn").disabled = true;
  $("#save-btn").textContent = "儲存中…";
  const payload = {
    crop_yt: state.cropYt,
    crop_reels: state.cropReels,
    deletions: [...state.deletions].sort((a, b) => a - b),
    head_trim_sec: state.headTrimSec,
    tail_trim_sec: state.tailTrimSec,
    cards: [...state.textOverrides.entries()].map(([idx, text]) => ({
      idx,
      text,
    })),
    // 只送 explicit 標記，carry-forward 推算結果不送；後端會 int(key) 還原
    cameras_mapping: Object.fromEntries(state.camerasMapping),
  };
  try {
    const r = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    $("#save-btn").textContent = "✅ 已儲存";
    // 儲存成功後既有的 undo 紀錄已落地，視為起點 → 清空 stacks
    clearUndoStacks();
    // 引導使用者按合成（兩個版本都高亮，使用者自行挑要先做哪一個）
    const ytBtn = $("#assemble-yt-btn");
    const reelsBtn = $("#assemble-reels-btn");
    ytBtn.classList.add("pulse");
    reelsBtn.classList.add("pulse");
    ytBtn.scrollIntoView({ block: "nearest", inline: "nearest" });
    setTimeout(() => {
      ytBtn.classList.remove("pulse");
      reelsBtn.classList.remove("pulse");
    }, 6000);
    setTimeout(() => {
      $("#save-btn").textContent = "完成並儲存";
      $("#save-btn").disabled = false;
    }, 2000);
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

const FILE_SECTIONS = [
  { kind: "main_video", label: "主影片", icon: "🎬" },
  { kind: "subtitle", label: "字幕", icon: "💬" },
  { kind: "composite", label: "合成輸出", icon: "📦" },
  { kind: "master", label: "母帶", icon: "🎙️" },
  { kind: "work", label: "工作檔", icon: "🛠️" },
  { kind: "other", label: "其他", icon: "📄" },
];

const COLLAPSE_KEY = "podcast-edit-collapsed-sections";

function loadCollapsedSections() {
  try {
    return new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || "[]"));
  } catch (e) {
    return new Set();
  }
}

function saveCollapsedSections(set) {
  localStorage.setItem(COLLAPSE_KEY, JSON.stringify(Array.from(set)));
}

function renderFileItem(f) {
  const item = document.createElement("div");
  item.className = "file-item";
  const isActive = state.previewPath === f.path;
  if (isActive) item.classList.add("previewing");

  const path = document.createElement("div");
  path.className = "file-path";
  path.title = f.path;
  path.textContent = f.path;

  // 字幕角色 badge
  const badges = document.createElement("span");
  badges.className = "file-badges";
  if (f.is_active_srt) {
    const b = document.createElement("span");
    b.className = "badge active";
    b.textContent = "使用中";
    badges.appendChild(b);
  }
  if (f.is_main_srt_backup) {
    const b = document.createElement("span");
    b.className = "badge muted";
    b.textContent = "原始備份";
    badges.appendChild(b);
  }

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
    const providerLabel =
      state.sttProvider === "gemini" ? "Gemini" : "xAI Grok";
    const hasSelectedKey =
      state.sttProvider === "gemini" ? state.hasGeminiKey : state.hasApiKey;
    action.title = hasSelectedKey
      ? `用 ${providerLabel} STT 轉字幕並覆蓋 _v2.srt`
      : `請先到 ⚙ 設定 ${providerLabel} API key`;
    action.addEventListener("click", () => requestTranscribe(f));
  } else {
    action = document.createElement("span");
    action.className = "file-stt-placeholder";
    action.textContent = "—";
  }

  item.append(path, badges, size, preview, action);
  return item;
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

  // 用 kind 分群（沒 kind 的 fallback 到 other）
  const groups = new Map();
  for (const f of state.files) {
    const k = f.kind || "other";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(f);
  }

  const collapsed = loadCollapsedSections();

  for (const section of FILE_SECTIONS) {
    const items = groups.get(section.kind) || [];
    if (items.length === 0) continue;

    const wrap = document.createElement("section");
    wrap.className = "file-section";
    wrap.dataset.kind = section.kind;

    const header = document.createElement("header");
    header.className = "file-section-header";
    const isCollapsed = collapsed.has(section.kind);
    header.innerHTML = `
      <span class="caret">${isCollapsed ? "▶" : "▼"}</span>
      <span class="section-icon">${section.icon}</span>
      <span class="section-label">${section.label}</span>
      <span class="section-count">${items.length}</span>
    `;
    header.addEventListener("click", () => {
      const cur = loadCollapsedSections();
      if (cur.has(section.kind)) cur.delete(section.kind);
      else cur.add(section.kind);
      saveCollapsedSections(cur);
      renderFiles();
    });
    wrap.appendChild(header);

    const inner = document.createElement("div");
    inner.className = "file-section-list" + (isCollapsed ? " hidden" : "");
    for (const f of items) inner.appendChild(renderFileItem(f));
    wrap.appendChild(inner);

    list.appendChild(wrap);
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

// === A1：拖放上傳到 01_母帶/ ===
const UPLOAD_EXTS = new Set([
  ".mp3",
  ".wav",
  ".m4a",
  ".flac",
  ".aac",
  ".ogg",
  ".opus",
  ".mp4",
  ".mov",
  ".mkv",
  ".webm",
]);

function setUploadStatus(msg, isError = false) {
  const el = $("#files-upload-status");
  if (!msg) {
    el.hidden = true;
    el.textContent = "";
    el.classList.remove("error");
    return;
  }
  el.hidden = false;
  el.textContent = msg;
  el.classList.toggle("error", isError);
}

async function uploadOne(file) {
  const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
  if (!UPLOAD_EXTS.has(ext)) {
    return { ok: false, name: file.name, error: `不支援的副檔名 ${ext}` };
  }
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    const r = await fetch("/api/upload", { method: "POST", body: form });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      return {
        ok: false,
        name: file.name,
        error: body.detail || `HTTP ${r.status}`,
      };
    }
    const body = await r.json();
    return { ok: true, name: file.name, path: body.path };
  } catch (e) {
    return { ok: false, name: file.name, error: e.message };
  }
}

async function handleUploadDrop(fileList) {
  const files = Array.from(fileList || []);
  if (files.length === 0) return;
  setUploadStatus(`上傳中 0 / ${files.length}…`);
  let done = 0;
  const errors = [];
  for (const f of files) {
    const res = await uploadOne(f);
    done += 1;
    if (res.ok) {
      setUploadStatus(`上傳中 ${done} / ${files.length}：${res.name} ✓`);
    } else {
      errors.push(`${res.name}：${res.error}`);
      setUploadStatus(`上傳中 ${done} / ${files.length}：${res.name} ✗`, true);
    }
  }
  await loadFiles();
  renderFiles();
  if (errors.length === 0) {
    setUploadStatus(`✓ 已上傳 ${files.length} 個檔案到 01_母帶/`);
    setTimeout(() => setUploadStatus(""), 3000);
  } else {
    setUploadStatus(
      `完成 ${done - errors.length} / ${files.length}，失敗：${errors.join("；")}`,
      true,
    );
  }
}

function initUploadDropZone() {
  const pane = $("#files-pane");
  if (!pane) return;
  let depth = 0;
  pane.addEventListener("dragenter", (e) => {
    if (!e.dataTransfer?.types?.includes("Files")) return;
    e.preventDefault();
    depth += 1;
    pane.classList.add("dragover");
  });
  pane.addEventListener("dragover", (e) => {
    if (!e.dataTransfer?.types?.includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  });
  pane.addEventListener("dragleave", () => {
    depth -= 1;
    if (depth <= 0) {
      depth = 0;
      pane.classList.remove("dragover");
    }
  });
  pane.addEventListener("drop", (e) => {
    if (!e.dataTransfer?.types?.includes("Files")) return;
    e.preventDefault();
    depth = 0;
    pane.classList.remove("dragover");
    handleUploadDrop(e.dataTransfer.files);
  });
  // 防止整個瀏覽器頁面被拖放檔案蓋掉（拖到 pane 之外時 fallback：直接忽略）
  ["dragover", "drop"].forEach((evt) => {
    document.addEventListener(evt, (e) => {
      if (!e.dataTransfer?.types?.includes("Files")) return;
      if (pane.contains(e.target)) return;
      e.preventDefault();
    });
  });
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
  const providerLabel = state.sttProvider === "gemini" ? "Gemini" : "xAI Grok";
  const hasSelectedKey =
    state.sttProvider === "gemini" ? state.hasGeminiKey : state.hasApiKey;
  if (!hasSelectedKey) {
    $("#transcribe-title").textContent = "尚未設定 API key";
    $("#transcribe-msg").innerHTML =
      `請先到右上角 ⚙ 設定 ${providerLabel} API key，才能轉字幕。`;
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
    `用 ${providerLabel} STT 轉字幕並覆寫 <code>_v2.srt</code>。<br>` +
    `預估時間：約音檔長度的 1 倍（3 分鐘片約 60–180 秒）。`;
  const go = $("#transcribe-go");
  go.textContent = "開始";
  go.disabled = false;
  go.onclick = () => runTranscribe(file);
  $("#transcribe-cancel").onclick = () => hideModal("transcribe-modal");
  showModal("transcribe-modal");
}

// 三段進度條：每段佔總長 1/3
const TRANSCRIBE_PHASES = ["compress", "upload", "resegment"];
const TRANSCRIBE_PHASE_LABELS = {
  compress: "壓縮音檔",
  upload: "上傳並等待 STT",
  resegment: "重新切句",
};
let _transcribePollTimer = null;

function stopTranscribePoll() {
  if (_transcribePollTimer) {
    clearInterval(_transcribePollTimer);
    _transcribePollTimer = null;
  }
}

function renderTranscribePhasePills(currentPhase, state) {
  // pending / active / done 三種狀態，依目前 phase 與 state 推導
  const curIdx = TRANSCRIBE_PHASES.indexOf(currentPhase);
  for (const phase of TRANSCRIBE_PHASES) {
    const el = document.querySelector(
      `#transcribe-progress .phase-pill[data-phase="${phase}"]`,
    );
    if (!el) continue;
    el.classList.remove("active", "done");
    const i = TRANSCRIBE_PHASES.indexOf(phase);
    if (state === "done") {
      el.classList.add("done");
    } else if (i < curIdx) {
      el.classList.add("done");
    } else if (i === curIdx) {
      el.classList.add("active");
    }
  }
}

function computeOverallPercent(phase, percent) {
  const idx = TRANSCRIBE_PHASES.indexOf(phase);
  if (idx < 0) return 0;
  return idx * (100 / 3) + Math.max(0, Math.min(100, percent)) / 3;
}

async function runTranscribe(file) {
  $("#transcribe-title").textContent = "轉字幕中…";
  $("#transcribe-msg").innerHTML =
    `處理中：<code>${file.path}</code><br>` +
    `<em style="color:#888;font-size:12px">請保留這個分頁，不要關閉。</em>`;
  $("#transcribe-progress").hidden = false;
  $("#transcribe-fill").style.width = "0%";
  $("#transcribe-percent").textContent = "0%";
  $("#transcribe-phase-label").textContent = "啟動中…";
  renderTranscribePhasePills(null, "running");

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
    if (!r.ok && r.status !== 202) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
  } catch (e) {
    finishTranscribe({ ok: false, error: e.message });
    return;
  }

  _transcribePollTimer = setInterval(pollTranscribe, 500);
}

async function pollTranscribe() {
  let s;
  try {
    const r = await fetch("/api/transcribe/status");
    s = await r.json();
  } catch (e) {
    return; // 暫時失敗，下次再試
  }

  if (s.state === "idle") return;

  if (s.state === "running") {
    const phase = s.phase || "compress";
    const pct = Math.max(0, Math.min(100, s.percent || 0));
    const overall = computeOverallPercent(phase, pct);
    $("#transcribe-fill").style.width = `${overall.toFixed(1)}%`;
    $("#transcribe-percent").textContent = `${overall.toFixed(0)}%`;
    $("#transcribe-phase-label").textContent =
      TRANSCRIBE_PHASE_LABELS[phase] || phase;
    renderTranscribePhasePills(phase, "running");
    return;
  }

  if (s.state === "done") {
    stopTranscribePoll();
    finishTranscribe({ ok: true, out_srt: s.out_srt });
    return;
  }

  if (s.state === "error") {
    stopTranscribePoll();
    finishTranscribe({ ok: false, error: s.error || "未知錯誤" });
    return;
  }
}

async function finishTranscribe({ ok, out_srt, error }) {
  const cancel = $("#transcribe-cancel");
  if (ok) {
    $("#transcribe-fill").style.width = "100%";
    $("#transcribe-percent").textContent = "100%";
    $("#transcribe-phase-label").textContent = "完成";
    renderTranscribePhasePills(null, "done");
    $("#transcribe-title").textContent = "✅ 完成";
    $("#transcribe-msg").innerHTML =
      `已寫入：<code>${out_srt || "_v2.srt"}</code><br>正在重新載入編輯區…`;

    await loadEpisodeState();
    renderTopbar();
    renderCards();
    renderCaption();
    renderTypo();
  } else {
    $("#transcribe-title").textContent = "❌ 失敗";
    $("#transcribe-msg").innerHTML =
      `<div style="color:#ff6b35">${error}</div>`;
    $("#transcribe-progress").hidden = true;
  }
  cancel.disabled = false;
  cancel.textContent = "關閉";
  cancel.onclick = () => {
    hideModal("transcribe-modal");
    cancel.textContent = "取消";
    $("#transcribe-progress").hidden = true;
  };
}

// === 合成流程 ===
// 流程：點 🎬 合成 YT 或 📱 合成 Reels → 直接以該 target 啟動
//      → POST /api/assemble {targets, force} → modal 直接進入進度模式 + 開始 polling
//      → done/error 各自渲染收尾畫面
// 400「輸出已存在」會 confirm 後自動以 force=true 重打
let _assemblePollTimer = null;

function fmtEta(s) {
  if (s == null) return "估算中…";
  if (s <= 0) return "完成";
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `剩餘 約 ${m} 分 ${r} 秒` : `剩餘 約 ${r} 秒`;
}

function stopAssemblePoll() {
  if (_assemblePollTimer) {
    clearInterval(_assemblePollTimer);
    _assemblePollTimer = null;
  }
}

// 把 modal 重設成「進度模式」初始畫面：欄位歸零、按鈕回到預設
function resetAssembleModal() {
  $("#assemble-fill").style.width = "0%";
  $("#assemble-percent").textContent = "0%";
  $("#assemble-eta").textContent = "—";
  $("#assemble-current-label").textContent = "準備中…";
  $("#assemble-eta-label").textContent = "—";
  $("#assemble-msg").textContent = "…";
  const reveal = $("#assemble-reveal");
  reveal.hidden = true;
  reveal.onclick = null;
  const cancel = $("#assemble-cancel");
  cancel.disabled = false;
  cancel.textContent = "取消";
  // cancel.onclick 在 setupAssembleButtons 內統一綁定
}

// 由「合成 YT」/「合成 Reels」按鈕呼叫，targets 是單一字串陣列
async function startAssemble(targets, { force = false } = {}) {
  $("#assemble-title").textContent = "合成中…";
  $("#assemble-msg").textContent =
    "ffmpeg 正在合成片頭 + 正片（含字幕與裁切）+ 片尾，請保留分頁。";

  try {
    const r = await fetch("/api/assemble", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ targets, force }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      const msg = body.detail || `HTTP ${r.status}`;
      // 400「輸出已存在」→ 提供覆寫選項，使用者同意就以 force=true 重打
      if (r.status === 400 && /輸出已存在|--force/.test(msg) && !force) {
        if (confirm(`${msg}\n\n要覆寫並重新合成嗎？`)) {
          return startAssemble(targets, { force: true });
        }
        hideModal("assemble-modal");
        return;
      }
      throw new Error(msg);
    }
  } catch (e) {
    $("#assemble-title").textContent = "❌ 無法啟動";
    $("#assemble-msg").innerHTML =
      `<div style="color:#ff6b35">${e.message}</div>`;
    return;
  }

  _assemblePollTimer = setInterval(pollAssemble, 1000);
}

async function pollAssemble() {
  let s;
  try {
    const r = await fetch("/api/assemble/status");
    s = await r.json();
  } catch (e) {
    return; // 暫時失敗，下次再試
  }

  if (s.state === "idle") {
    // 還沒開始或已重置，避免覆蓋 done 後的畫面
    return;
  }

  if (s.state === "running") {
    const pct = Math.max(0, Math.min(100, s.percent || 0));
    const targetName = s.current === "yt" ? "YT" : "Reels";
    let label;
    if ((s.total || 0) > 1) {
      label = `[${(s.index || 0) + 1}/${s.total}] ${targetName} 合成中… ${pct.toFixed(1)}%`;
    } else {
      label = `${targetName} 合成中… ${pct.toFixed(1)}%`;
    }
    $("#assemble-current-label").textContent = label;
    $("#assemble-percent").textContent = `${pct.toFixed(1)}%`;
    $("#assemble-eta").textContent = fmtEta(s.eta_s);
    $("#assemble-eta-label").textContent = fmtEta(s.eta_s);
    $("#assemble-fill").style.width = `${pct.toFixed(1)}%`;
    return;
  }

  if (s.state === "done") {
    stopAssemblePoll();
    $("#assemble-title").textContent = "✅ 合成完成";
    const outs = s.output_files || [];
    if (outs.length === 0) {
      $("#assemble-msg").innerHTML = "已完成（找不到輸出檔資訊）";
    } else {
      const lines = outs.map((p) => `<code>${p}</code>`).join("<br>");
      $("#assemble-msg").innerHTML = `已輸出：<br>${lines}`;
    }
    const reveal = $("#assemble-reveal");
    const revealPath = async (p) => {
      await fetch("/api/reveal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: p }),
      });
    };
    if (outs.length > 0) {
      reveal.hidden = false;
      reveal.onclick = async () => {
        try {
          await revealPath(outs[0]);
        } catch (e) {
          alert(`開啟失敗：${e.message}`);
        }
      };
      // 自動 reveal 第一個輸出 — 使用者剛在等合成結果，跳 Finder 是合理回饋；
      // 失敗就靜默退回手動按鈕（按鈕本身仍可重試）
      revealPath(outs[0]).catch(() => {});
    }
    // 重新載入專案檔案列表，讓新合成檔出現在右側
    try {
      await loadFiles();
      renderFiles();
    } catch (_) {}
    return;
  }

  if (s.state === "error") {
    stopAssemblePoll();
    $("#assemble-title").textContent = "❌ 合成失敗";
    $("#assemble-msg").innerHTML =
      `<div style="color:#ff6b35;white-space:pre-wrap">${s.error || "未知錯誤"}</div>`;
    return;
  }
}

// 集中綁定 assemble 相關 listener，啟動時呼叫一次
function setupAssembleButtons() {
  const launch = (targets, title) => {
    const dirty =
      state.deletions.size > 0 ||
      state.textOverrides.size > 0 ||
      state.cropYt != null ||
      state.cropReels != null;
    if (dirty) {
      if (
        !confirm(
          "有未儲存的修改，建議先按「完成並儲存」再合成。\n仍要直接合成嗎？（會用磁碟上的 _v2.srt）",
        )
      ) {
        return;
      }
    }
    resetAssembleModal();
    $("#assemble-title").textContent = title;
    showModal("assemble-modal");
    startAssemble(targets);
  };

  $("#assemble-yt-btn").addEventListener("click", () => {
    launch(["yt"], "合成 YT 16:9 完整版");
  });
  $("#assemble-reels-btn").addEventListener("click", () => {
    launch(["reels"], "合成 Reels 9:16 短版");
  });

  $("#assemble-cancel").addEventListener("click", () => {
    stopAssemblePoll();
    hideModal("assemble-modal");
  });
}

// === 設定 modal ===
function openSettings() {
  $("#settings-xai-key").value = "";
  $("#settings-xai-key").type = "password";
  $("#settings-gemini-key").value = "";
  $("#settings-gemini-key").type = "password";
  $("#settings-xai-status").textContent = state.hasApiKey
    ? "已存在（重新輸入會覆蓋；留空則維持原樣）"
    : "尚未設定";
  $("#settings-gemini-status").textContent = state.hasGeminiKey
    ? "已存在（重新輸入會覆蓋；留空則維持原樣）"
    : "尚未設定";
  const provider = state.sttProvider || "xai";
  const radio = document.querySelector(
    `input[name="settings-provider"][value="${provider}"]`,
  );
  if (radio) radio.checked = true;
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

$("#settings-show-gemini").addEventListener("click", () => {
  const input = $("#settings-gemini-key");
  input.type = input.type === "password" ? "text" : "password";
});

$("#settings-save").addEventListener("click", async () => {
  const xaiKey = $("#settings-xai-key").value.trim();
  const geminiKey = $("#settings-gemini-key").value.trim();
  const provider =
    document.querySelector('input[name="settings-provider"]:checked')?.value ||
    "xai";
  const payload = { provider };
  if (xaiKey) payload.xai_api_key = xaiKey;
  if (geminiKey) payload.gemini_api_key = geminiKey;
  const btn = $("#settings-save");
  btn.disabled = true;
  btn.textContent = "儲存中…";
  try {
    const r = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const body = await r.text();
      throw new Error(`HTTP ${r.status}：${body}`);
    }
    const data = await r.json();
    state.hasApiKey = !!data.has_xai_api_key;
    state.hasGeminiKey = !!data.has_gemini_api_key;
    state.sttProvider = data.provider || "xai";
    hideModal("settings-modal");
    renderFiles();
  } catch (e) {
    alert(`儲存失敗：${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "儲存";
  }
});

// === 雙鏡頭 cam B 設定 modal（T23a-followup：消除手改 yaml） ===
function openCamModal() {
  const sel = $("#cam-b-select");
  sel.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "（無，單鏡頭）";
  sel.appendChild(none);
  const currentB = (state.cameras && state.cameras.b) || "";
  // 把目前 b（若不在候選清單）也加進來，避免被自動 reset
  const opts = new Set(state.camBCandidates || []);
  if (currentB) opts.add(currentB);
  for (const path of [...opts].sort()) {
    const o = document.createElement("option");
    o.value = path;
    o.textContent = path;
    if (path === currentB) o.selected = true;
    sel.appendChild(o);
  }
  $("#cam-sync-offset-b").value = state.camSyncOffsetB
    ? String(state.camSyncOffsetB)
    : "";
  showModal("cam-modal");
}

$("#cam-btn").addEventListener("click", openCamModal);
$("#cam-cancel").addEventListener("click", () => hideModal("cam-modal"));

// T23b: 自動對齊（音訊互相關）。前端只負責叫 endpoint + 把結果填回 input；
// 寫 yaml 仍走「儲存」按鈕，避免 race + 跟現有設計一致。
$("#cam-auto-align").addEventListener("click", async () => {
  const camBPath = $("#cam-b-select").value || "";
  if (!camBPath) {
    alert("請先選 cam B 來源");
    return;
  }
  const btn = $("#cam-auto-align");
  btn.disabled = true;
  btn.textContent = "計算中…";
  try {
    const r = await fetch("/api/auto-align", { method: "POST" });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    $("#cam-sync-offset-b").value = data.offset_sec.toFixed(3);
  } catch (e) {
    alert(`自動對齊失敗：${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "🎯 自動對齊";
  }
});

// T23c: 手動標記三檔聲音事件 → 算 offset。fallback for T23b 不準時。
function _manualAlignRender() {
  const rows = $("#manual-align-rows");
  rows.innerHTML = "";
  for (let i = 1; i <= 3; i++) {
    const row = document.createElement("div");
    row.className = "modal-row";
    row.style.marginBottom = "8px";
    row.innerHTML =
      `<span style="min-width:64px; display:inline-block">事件 ${i}</span>` +
      `<input type="number" id="manual-a-${i}" step="0.01" ` +
      `placeholder="cam A 秒數" style="margin-right:8px" />` +
      `<input type="number" id="manual-b-${i}" step="0.01" ` +
      `placeholder="cam B 秒數" />`;
    rows.appendChild(row);
  }
  $("#manual-align-result").hidden = true;
  $("#manual-align-error").hidden = true;
  $("#manual-align-apply").disabled = true;
  $("#manual-align-apply").dataset.offset = "";
}

$("#cam-manual-align").addEventListener("click", () => {
  _manualAlignRender();
  $("#manual-align-modal").classList.remove("hidden");
});

$("#manual-align-cancel").addEventListener("click", () => {
  $("#manual-align-modal").classList.add("hidden");
});

$("#manual-align-compute").addEventListener("click", async () => {
  const events = [];
  for (let i = 1; i <= 3; i++) {
    const a = $(`#manual-a-${i}`).value;
    const b = $(`#manual-b-${i}`).value;
    if (a === "" || b === "") {
      $("#manual-align-error").textContent = `事件 ${i} 兩邊都要填`;
      $("#manual-align-error").hidden = false;
      $("#manual-align-result").hidden = true;
      $("#manual-align-apply").disabled = true;
      return;
    }
    events.push({ a: Number(a), b: Number(b) });
  }
  const btn = $("#manual-align-compute");
  btn.disabled = true;
  btn.textContent = "計算中…";
  try {
    const r = await fetch("/api/manual-align", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    const offset = data.offset_sec;
    const deltas = data.deltas || [];
    const deltaStr = deltas
      .map((d, i) => `事件${i + 1} 離差 ${d >= 0 ? "+" : ""}${d.toFixed(3)}s`)
      .join("｜");
    const maxAbs = deltas.length
      ? Math.max(...deltas.map((d) => Math.abs(d)))
      : 0;
    const hint =
      maxAbs > 0.2
        ? "（離差 > 0.2s，建議重標一次）"
        : maxAbs > 0.05
          ? "（離差略大，可接受）"
          : "（三筆很一致）";
    $("#manual-align-result").innerHTML =
      `算出 offset = <b>${offset.toFixed(3)}s</b><br/>${deltaStr} ${hint}`;
    $("#manual-align-result").hidden = false;
    $("#manual-align-error").hidden = true;
    $("#manual-align-apply").disabled = false;
    $("#manual-align-apply").dataset.offset = offset.toFixed(3);
  } catch (e) {
    $("#manual-align-error").textContent = `計算失敗：${e.message}`;
    $("#manual-align-error").hidden = false;
    $("#manual-align-result").hidden = true;
    $("#manual-align-apply").disabled = true;
  } finally {
    btn.disabled = false;
    btn.textContent = "算 offset";
  }
});

$("#manual-align-apply").addEventListener("click", () => {
  const offset = $("#manual-align-apply").dataset.offset;
  if (offset === "") return;
  $("#cam-sync-offset-b").value = offset;
  $("#manual-align-modal").classList.add("hidden");
});

$("#cam-save").addEventListener("click", async () => {
  const camBPath = $("#cam-b-select").value || "";
  const offsetRaw = $("#cam-sync-offset-b").value;
  const offset = offsetRaw === "" ? 0 : Number(offsetRaw);
  if (!Number.isFinite(offset)) {
    alert("同步偏移要是數字");
    return;
  }
  const btn = $("#cam-save");
  btn.disabled = true;
  btn.textContent = "儲存中…";
  // 只送 cam B 相關欄位 + 必填的 deletions/cards（保留現有編輯）
  const payload = {
    crop_yt: state.cropYt,
    crop_reels: state.cropReels,
    deletions: [...state.deletions].sort((a, b) => a - b),
    head_trim_sec: state.headTrimSec,
    tail_trim_sec: state.tailTrimSec,
    cards: [...state.textOverrides.entries()].map(([idx, text]) => ({
      idx,
      text,
    })),
    cameras_mapping: Object.fromEntries(state.camerasMapping),
    cam_b_path: camBPath,
    camera_sync_offset_b: offset,
  };
  try {
    const r = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    // 重抓 episode state 讓 A/B toggle 即刻反映新 cameras
    await loadEpisodeState();
    renderTopbar();
    renderCards();
    hideModal("cam-modal");
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
    state.cropYt != null ||
    state.cropReels != null;
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
    state.cropRatioYt = null;
    state.cropRatioReels = null;
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

// === A3：新建集 wizard ===
function todayYmd() {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}${m}${day}`;
}

function updateNewEpPreview() {
  const date = $("#new-ep-date").value.trim();
  const name = $("#new-ep-name").value.trim();
  const preview = $("#new-ep-preview");
  const err = $("#new-ep-error");
  err.hidden = true;
  err.textContent = "";
  if (!date && !name) {
    preview.textContent = "→ 例：20260604 第 12 集";
    return;
  }
  preview.textContent = `→ ${date || "YYYYMMDD"} ${name || "集名"}`;
}

function openNewEpModal() {
  const dirty = state.deletions.size > 0 || state.textOverrides.size > 0;
  if (dirty && !confirm("有未儲存的修改，新建集後會丟失，繼續？")) return;
  $("#new-ep-date").value = todayYmd();
  $("#new-ep-name").value = "";
  $("#new-ep-error").hidden = true;
  $("#new-ep-error").textContent = "";
  updateNewEpPreview();
  $("#new-ep-modal").classList.remove("hidden");
  $("#new-ep-name").focus();
}

function closeNewEpModal() {
  $("#new-ep-modal").classList.add("hidden");
}

async function submitNewEpisode() {
  const date = $("#new-ep-date").value.trim();
  const name = $("#new-ep-name").value.trim();
  const err = $("#new-ep-error");
  const goBtn = $("#new-ep-go");
  const cancelBtn = $("#new-ep-cancel");

  err.hidden = true;
  err.textContent = "";

  if (!(date.length === 8 && /^\d{8}$/.test(date))) {
    err.textContent = "日期要 8 位數字（YYYYMMDD）";
    err.hidden = false;
    return;
  }
  if (!name) {
    err.textContent = "請輸入集名";
    err.hidden = false;
    return;
  }
  if (/[/\\]/.test(name)) {
    err.textContent = "集名不可包含 / \\";
    err.hidden = false;
    return;
  }

  const origGo = goBtn.textContent;
  goBtn.disabled = true;
  cancelBtn.disabled = true;
  goBtn.textContent = "建立中…";
  try {
    const r = await fetch("/api/episode/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date, name }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
    closeNewEpModal();
    // 後端已 switch；前端 reload 所有狀態 + cache-bust 影片
    state.previewPath = null;
    state.cropRatioYt = null;
    state.cropRatioReels = null;
    const video = $("#video");
    video.src = `/api/video?_=${Date.now()}`;
    video.load();
    await load();
  } catch (e) {
    err.textContent = `失敗：${e.message}`;
    err.hidden = false;
  } finally {
    goBtn.disabled = false;
    cancelBtn.disabled = false;
    goBtn.textContent = origGo;
  }
}

$("#ep-new-btn").addEventListener("click", openNewEpModal);
$("#new-ep-cancel").addEventListener("click", closeNewEpModal);
$("#new-ep-go").addEventListener("click", submitNewEpisode);
$("#new-ep-date").addEventListener("input", updateNewEpPreview);
$("#new-ep-name").addEventListener("input", updateNewEpPreview);
$("#new-ep-name").addEventListener("keydown", (e) => {
  if (e.key === "Enter") submitNewEpisode();
});
$("#new-ep-date").addEventListener("keydown", (e) => {
  if (e.key === "Enter") submitNewEpisode();
});

setupVersionTabs();
setupAssembleButtons();
setupSusToolbar();
