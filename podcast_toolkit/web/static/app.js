// 編輯狀態：全部存在這裡，存檔時一次 POST。
const state = {
  name: "",
  activeVersion: "yt", // "yt" | "reels"
  cropYt: null,
  cropReels: null,
  // cam B 獨立 crop（雙機集才用）；null = fallback 用 cropYt / cropReels
  // ratio 仍是 per-version 共享，因為輸出尺寸固定 → 兩鏡頭 crop aspect 必須一致
  cropYtB: null,
  cropReelsB: null,
  cropRatioYt: null, // "4:5" | "9:16" | "16:9" | null
  cropRatioReels: null,
  deletions: new Set(),
  susChecked: new Set(), // 紅卡批次刪除的 checkbox 勾選集合（card.idx）
  cards: [],
  textOverrides: new Map(), // idx -> text
  // 在 UI 上按 Enter 切句：oldIdx -> [part0_text, part1_text, ...]；存檔時翻譯成
  // SRT 新編號，並把 deletions / camerasMapping 從 "5" 散成 "5:0" / "5:1"。
  // 切過的卡 textOverrides 會被清掉（parts 內容才是真相）。
  // sub-card 上按 Enter 可連鎖切：把該 part 拆兩半、後面 composite key 全部 +1。
  cardSplits: new Map(),
  typoDict: [], // [{wrong, right, note}]
  files: [], // [{path, size, transcribable, previewable}]
  previewPath: null, // null = main_video；否則為 ep.dir 內的相對路徑
  hasGeminiKey: false,
  hasOpenAIKey: false,
  sttProvider: "gemini", // "gemini" | "openai"
  needsTranscribe: false, // true 代表這集還沒跑過 transcribe/resegment，沒 _v2.srt
  hasMainVideo: true, // false = 空集（01_母帶/ 還沒有檔），video player 換成 empty banner
  headTrimSec: 0, // 影片開頭要砍掉幾秒
  tailTrimSec: 0, // 影片結尾要砍掉幾秒
  // 雙鏡頭：cameras = {a, b?}（僅雙機集才有 b），用來判斷 UI 是否要顯示 A/B toggle
  cameras: {},
  // 字幕卡 idx -> "a" | "b"，只記 explicit 標過的；其他卡靠 carry-forward 推算
  camerasMapping: new Map(),
  // T23a-followup：cam B UI 用，避免使用者手改 yaml
  camBCandidates: [], // 後端掃 01_母帶/*.mp4 排除 cam A
  camSyncOffsetB: 0, // 秒；cam B 相對 cam A 的對齊偏移
  // 分軌 mic：mics = {a, b, ...}（單軌集為空 dict）；前端據此判斷要不要渲染 speaker UI
  mics: {},
  // 字幕卡 idx -> speaker key（"a" / "b" / ...），來自 srt_merge 產出的 speakers.json sidecar
  // 同 camerasMapping 形狀，但 speaker 不做 carry-forward（每張卡都有明確 speaker）
  speakersMapping: new Map(),
  // Reels 片段：list of {name, start_card, end_card}（1-indexed card idx）
  reelsClips: [],
  // 字幕預覽用：對齊 ffmpeg ASS 實際輸出（font_size / output_height）
  // 缺值時 fallback 到合理預設，避免換集瞬間預覽爆炸
  subtitleStyleYt: null,
  subtitleStyleReels: null,
  outputResYt: { w: 1920, h: 1080 },
  outputResReels: { w: 1080, h: 1920 },
  // Undo / Redo：只追蹤會進 episode.yaml 的編輯狀態
  // 換集 / 儲存成功 → 一律清空兩 stacks（與 dirty 概念對齊）
  undoStack: [],
  redoStack: [],
};

const UNDO_MAX = 100;

// 預覽 overlay 是否正在顯示 cam B → 決定 crop UI 在編 A 還是 B
function isCamBOverlayActive() {
  const camb = document.querySelector("#video-camb");
  return !!(camb && camb.classList.contains("active"));
}
function getActiveCropCam() {
  return isCamBOverlayActive() ? "b" : "a";
}
function _baseCrop() {
  return state.activeVersion === "yt" ? state.cropYt : state.cropReels;
}
function _bCrop() {
  return state.activeVersion === "yt" ? state.cropYtB : state.cropReelsB;
}
function getActiveCrop() {
  // 編 B 時：有 override 就用 override；沒就 fallback 用 base（顯示用，不會寫回）
  if (getActiveCropCam() === "b") return _bCrop() || _baseCrop();
  return _baseCrop();
}
function setActiveCrop(crop) {
  const cam = getActiveCropCam();
  if (state.activeVersion === "yt") {
    if (cam === "b") state.cropYtB = crop;
    else state.cropYt = crop;
  } else {
    if (cam === "b") state.cropReelsB = crop;
    else state.cropReels = crop;
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
// 寫回 yaml 用：base + 可選 .b override 合成單一 dict；base null → null
function serializeCropForSave(base, b) {
  if (!base) return null;
  return b ? { ...base, b: { ...b } } : { ...base };
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
    cropYtB: state.cropYtB ? { ...state.cropYtB } : null,
    cropReelsB: state.cropReelsB ? { ...state.cropReelsB } : null,
    cropRatioYt: state.cropRatioYt,
    cropRatioReels: state.cropRatioReels,
    camerasMapping: new Map(state.camerasMapping),
    speakersMapping: new Map(state.speakersMapping),
    headTrimSec: state.headTrimSec,
    tailTrimSec: state.tailTrimSec,
    reelsClips: state.reelsClips.map((c) => ({ ...c })),
    cardSplits: new Map([...state.cardSplits].map(([k, v]) => [k, v.slice()])),
  };
}

function applyEditSnapshot(snap) {
  state.deletions = new Set(snap.deletions);
  state.textOverrides = new Map(snap.textOverrides);
  state.cropYt = snap.cropYt ? { ...snap.cropYt } : null;
  state.cropReels = snap.cropReels ? { ...snap.cropReels } : null;
  state.cropYtB = snap.cropYtB ? { ...snap.cropYtB } : null;
  state.cropReelsB = snap.cropReelsB ? { ...snap.cropReelsB } : null;
  state.cropRatioYt = snap.cropRatioYt;
  state.cropRatioReels = snap.cropRatioReels;
  state.camerasMapping = new Map(snap.camerasMapping);
  state.speakersMapping = new Map(snap.speakersMapping || []);
  state.headTrimSec = snap.headTrimSec;
  state.tailTrimSec = snap.tailTrimSec;
  state.reelsClips = (snap.reelsClips || []).map((c) => ({ ...c }));
  state.cardSplits = new Map(
    [...(snap.cardSplits || [])].map(([k, v]) => [k, v.slice()]),
  );
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
  renderReelsClips();
  // crop ratio 按鈕在 setupCrop IIFE 內，沒 export → 這裡重算
  document.querySelectorAll(".ratio-btn").forEach((btn) => {
    btn.classList.toggle(
      "active",
      getActiveCropRatio() === btn.dataset.ratio && getActiveCrop() != null,
    );
  });
  // undo/redo 拉回 camerasMapping 後也要立刻反映在 overlay 上
  if (typeof refreshCamBOverlay === "function") refreshCamBOverlay();
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

// 集中判斷「有沒有未儲存變動」，topbar chip / beforeunload / cancel / 換集 / 合成都用這個
// 包含：刪除 / 改字 / 切句 / 裁切框。trim / cam mapping 走別的儲存通道不算進來
function hasUnsavedChanges() {
  return (
    state.deletions.size > 0 ||
    state.textOverrides.size > 0 ||
    state.cardSplits.size > 0 ||
    state.cropYt != null ||
    state.cropReels != null
  );
}

function unsavedCount() {
  return (
    state.deletions.size +
    state.textOverrides.size +
    state.cardSplits.size +
    (state.cropYt != null ? 1 : 0) +
    (state.cropReels != null ? 1 : 0)
  );
}

function renderTopbar() {
  $("#title").textContent = state.name;
  const badge = $("#unsaved-badge");
  if (state.needsTranscribe) {
    $("#status").textContent = "尚未轉字幕";
    $("#save-btn").disabled = true;
    if (badge) badge.classList.add("hidden");
    return;
  }
  const total = state.cards.length;
  const deleted = state.deletions.size;
  const dirty = state.textOverrides.size;
  const split = state.cardSplits.size;
  const head = state.headTrimSec || 0;
  const tail = state.tailTrimSec || 0;
  let line = `字幕卡 ${total} 段 · 已刪 ${deleted} · 已修 ${dirty}`;
  if (split > 0) line += ` · 已切 ${split}`;
  if (head > 0 || tail > 0) {
    line += ` · 頭 ${head.toFixed(1)}s / 尾 ${tail.toFixed(1)}s`;
  }
  $("#status").textContent = line;
  const allDeleted = total > 0 && deleted === total;
  $("#save-btn").disabled = allDeleted;

  // 未儲存 chip：有變更才亮，數字顯示總變動筆數
  if (badge) {
    const n = unsavedCount();
    if (n > 0) {
      badge.classList.remove("hidden");
      $("#unsaved-count").textContent = String(n);
    } else {
      badge.classList.add("hidden");
    }
  }
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

  // 兩個拖把：影片載入後才能定位（沒 duration 時藏起來，避免拖到沒意義的位置）
  const headHandle = $("#trim-handle-head");
  const tailHandle = $("#trim-handle-tail");
  if (dur > 0) {
    const headPct = Math.min(100, Math.max(0, (head / dur) * 100));
    const tailPct = Math.min(100, Math.max(0, ((dur - tail) / dur) * 100));
    headHandle.style.left = `${headPct.toFixed(2)}%`;
    tailHandle.style.left = `${tailPct.toFixed(2)}%`;
    headHandle.style.display = "block";
    tailHandle.style.display = "block";
  } else {
    headHandle.style.display = "none";
    tailHandle.style.display = "none";
  }

  const hint = $("#trim-hint");
  if (head > 0 || tail > 0) {
    const remain = Math.max(0, dur - head - tail);
    hint.textContent = `保留 ${remain.toFixed(1)}s / 總長 ${dur.toFixed(1)}s`;
  } else {
    hint.textContent = "把播放游標停在要切的位置再按設頭 / 設尾，或直接拖把";
  }
}

// 算 caption preview 字體 px：對齊 ffmpeg ASS 輸出（font_size / output_height）
// 預覽字幕應該 ∝ 影片框實際高度 × crop 高度比 × (字級 / 輸出高度)
// 這樣不管瀏覽器 zoom 多少、視窗縮多大，字幕跟畫面的比例都跟最終輸出一致
function computeCaptionFontPx() {
  const wrap = document.querySelector(".video-wrap");
  if (!wrap) return null;
  const wrapHeight = wrap.clientHeight;
  if (!wrapHeight) return null;
  const isReels = state.activeVersion === "reels";
  const style = isReels
    ? state.subtitleStyleReels || state.subtitleStyleYt
    : state.subtitleStyleYt;
  const res = isReels ? state.outputResReels : state.outputResYt;
  // 缺資料就維持原 clamp 預設（loadEpisodeState 完成前）
  if (!style || !res) return null;
  const fontSize = Number(style.font_size);
  const outH = Number(res.h);
  if (!fontSize || !outH) return null;
  const c = getActiveCrop();
  // 有 crop：用 crop 區的渲染高（= wrap高 × crop.height）
  // 無 crop：用整個 wrap 高（= 整個源 frame，YT 直接代表 1080 輸出）
  const baseHeight = c ? wrapHeight * c.height : wrapHeight;
  return baseHeight * (fontSize / outH);
}

function applyCaptionFontSize() {
  const overlay = document.querySelector("#caption-overlay");
  if (!overlay) return;
  const px = computeCaptionFontPx();
  overlay.style.fontSize = px ? `${px.toFixed(2)}px` : "";
}

function renderCropInfo() {
  const c = getActiveCrop();
  const overlay = $("#caption-overlay");
  // Reels 字幕走畫面正中央（對齊 subtitle_style_reels.alignment=10/SSA mid-center）
  // margin_v 正值=從中心向下偏移（output px），預覽要同步換算成裁切框內比例
  const isReels = state.activeVersion === "reels";
  const reelsMarginV = Number(state.subtitleStyleReels?.margin_v ?? 0);
  const reelsOutH = Number(state.outputResReels?.h ?? 1920) || 1920;
  const reelsMarginFrac = reelsMarginV / reelsOutH;
  // 只有雙機集才顯示鏡頭徽章；單機集 cam B 一直沒值就略過徽章
  const hasCamB = !!(state.cameras && state.cameras.b);
  let camBadge = "";
  if (hasCamB) {
    const cam = getActiveCropCam();
    if (cam === "b") {
      camBadge = _bCrop() ? "（B 獨立）· " : "（B 沿用 A）· ";
    } else {
      camBadge = "（A）· ";
    }
  }
  if (!c) {
    $("#crop-text").textContent = `裁切框：${camBadge}未設定（整張畫面）`;
    $("#crop-frame").classList.add("hidden");
    // 字幕回到整個影片區
    overlay.style.left = "";
    overlay.style.right = "";
    if (isReels) {
      overlay.style.bottom = "";
      overlay.style.top = `${(50 + reelsMarginFrac * 100).toFixed(2)}%`;
      overlay.style.transform = "translateY(-50%)";
    } else {
      overlay.style.top = "";
      overlay.style.transform = "";
      overlay.style.bottom = "";
    }
    applyCaptionFontSize();
    return;
  }
  const ratio = getActiveCropRatio() ? `${getActiveCropRatio()}` : "自訂";
  $("#crop-text").textContent =
    `裁切框：${camBadge}${ratio} · x=${(c.x * 100).toFixed(0)}% y=${(c.y * 100).toFixed(0)}%`;
  const frame = $("#crop-frame");
  frame.classList.remove("hidden");
  frame.style.left = `${c.x * 100}%`;
  frame.style.top = `${c.y * 100}%`;
  frame.style.width = `${c.width * 100}%`;
  frame.style.height = `${c.height * 100}%`;

  // 字幕鎖在裁切框內：左右各內縮 6% 裁切寬度
  const padX = 0.06;
  overlay.style.left = `${((c.x + c.width * padX) * 100).toFixed(2)}%`;
  overlay.style.right = `${((1 - c.x - c.width + c.width * padX) * 100).toFixed(2)}%`;
  if (isReels) {
    // Reels：放在裁切框垂直中央 + margin_v 換算成裁切框內比例向下偏移
    // 注意：libass 燒在最終 output frame 上，margin_v 是 output px；
    // 預覽裁切框 = output frame，故偏移量 = margin_v/output_h × c.height
    const cy = (c.y + c.height * (0.5 + reelsMarginFrac)) * 100;
    overlay.style.bottom = "";
    overlay.style.top = `${cy.toFixed(2)}%`;
    overlay.style.transform = "translateY(-50%)";
  } else {
    // YT：距框底 8% 裁切高度
    const padBottom = 0.08;
    overlay.style.top = "";
    overlay.style.transform = "";
    overlay.style.bottom = `${((1 - c.y - c.height + c.height * padBottom) * 100).toFixed(2)}%`;
  }
  // 字幕大小對齊 ffmpeg ASS 實際輸出（font_size / output_height × 渲染高）
  applyCaptionFontSize();
}

// 回傳 expandedCards() 中包住 t 的那筆 — 切過的卡會在這裡命中對應 sub-card，
// 預覽/highlight/cam-B 切換都靠這個吃 composite key 與切後時間。
// tight-pack 模式下 sub-cards 尾段沒分到字幕（trailing silence），
// 若 t 落在原 cue 範圍內但沒命中 sub-card → 回傳該 cue 的最後一張 sub-card，
// 避免 highlight 在原 cue 中段突然消失，看起來「對不上 / 亂跳」。
function activeCardAt(t) {
  const exp = expandedCards();
  for (const r of exp) {
    if (t >= r.start && t < r.end) return r;
  }
  // fallback：t 落在某個原 cue 的尾段空窗（partDur 之和 < 原 cue dur）→ 找最後一張 sub-card
  for (let i = exp.length - 1; i >= 0; i--) {
    const r = exp[i];
    if (r.partIdx == null) continue;
    if (t >= r.c.start && t < r.c.end) return r;
  }
  return null;
}

// 分軌版：拿出 t 當下所有 active 卡（可能不只一張：兩人同時講話 → 兩張不同 speaker 的卡同時在跑）。
// 單軌集 / 沒重疊 → 回 [activeCardAt] 退化結果，給 renderCaption 統一邏輯用。
function activeCardsAt(t) {
  const exp = expandedCards();
  const hits = exp.filter((r) => t >= r.start && t < r.end);
  if (hits.length) return hits;
  // fallback 同 activeCardAt：尾段空窗找最後一張 sub-card
  for (let i = exp.length - 1; i >= 0; i--) {
    const r = exp[i];
    if (r.partIdx == null) continue;
    if (t >= r.c.start && t < r.c.end) return [r];
  }
  return [];
}

// Enter 切完／Backspace 合併完之後 re-render，把 caret 移到指定 card / sub-card 的指定 offset
// dataIdx：未切卡傳 int c.idx；sub-card 傳 "<idx>:<part>" 字串
// offset：caret 字元位置（Enter 預設 0；Backspace 合併要落在交界處）
function focusCardAt(dataIdx, offset = 0) {
  renderTopbar();
  renderCards();
  renderCaption();
  renderTypo();
  setTimeout(() => {
    const next = document.querySelector(
      `#cards-list .card[data-idx="${dataIdx}"] .card-text`,
    );
    if (!next) return;
    next.focus();
    const sel = window.getSelection();
    const range = document.createRange();
    const node = next.firstChild || next;
    const max = node.nodeType === 3 ? node.textContent.length : 0;
    const safe = Math.min(Math.max(offset, 0), max);
    range.setStart(node, safe);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
  }, 0);
}

function focusSplitTarget(parentIdx, targetPart) {
  focusCardAt(`${parentIdx}:${targetPart}`, 0);
}

// 算 contentEditable 內 caret 距離元素開頭的字元數（用於 Enter 切卡判斷游標位置）
function getCursorOffset(el) {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return 0;
  const range = sel.getRangeAt(0);
  if (!el.contains(range.startContainer)) return 0;
  const pre = range.cloneRange();
  pre.selectNodeContents(el);
  pre.setEnd(range.startContainer, range.startOffset);
  return pre.toString().length;
}

// 把 state.cards 展開成 render 用的扁平清單；切過的卡會展開成多張 sub-card。
// 每筆：{c: 原 card, partIdx: 0..N-1 or null, key: deletions/camerasMapping 用的 id,
//        text: 顯示文字, start: 顯示開始秒, end: 顯示結束秒}
// 未切的卡 key 是 int c.idx（向後相容既有 state.deletions int 鍵）；
// 切過的卡 key 是 "<idx>:<partIdx>"（後端 _parse_composite_id 兩種都吃）。
// 切卡時 sub-card 時間分配規則：
//   原卡 dur > 字數 * SEC_PER_CHAR → 從 t0 緊湊排，尾段 trailing silence 不分配（overlay 顯示空）
//   原卡 dur 比 budget 還小 → 比例分配貼滿整段
// 對應後端 srt_io.allocate_split_times，兩邊規則必須一致避免存檔前後 UI 跳動。
const SPLIT_SEC_PER_CHAR = 0.3;
function expandedCards() {
  const out = [];
  for (const c of state.cards) {
    const parts = state.cardSplits.get(c.idx);
    if (parts && parts.length > 1) {
      const lengths = parts.map((p) => Math.max((p || "").length, 1));
      const total = lengths.reduce((a, b) => a + b, 0);
      const t0 = c.start;
      const t1 = c.end;
      const dur = t1 - t0;
      const budget = total * SPLIT_SEC_PER_CHAR;
      const rate = budget <= dur ? SPLIT_SEC_PER_CHAR : dur / total;
      let cum = 0;
      for (let i = 0; i < parts.length; i++) {
        const start = t0 + cum;
        cum += lengths[i] * rate;
        const end = Math.min(t0 + cum, t1);
        out.push({
          c,
          partIdx: i,
          key: `${c.idx}:${i}`,
          text: parts[i],
          start,
          end,
        });
      }
    } else {
      out.push({
        c,
        partIdx: null,
        key: c.idx,
        text: state.textOverrides.get(c.idx) ?? c.text,
        start: c.start,
        end: c.end,
      });
    }
  }
  return out;
}

// 算這張卡實際生效的 speaker：speakers sidecar 是每張卡都有明確值
// （由 srt_merge 從 N 路 mic SRT merge 出來），不需要 carry-forward。
// 沒值 = 單軌集或 sidecar 缺漏 → 回 null，UI 隱藏 speaker tag / ruler。
// 切過的卡：sub-card 都繼承原卡的 speaker（切句不會切換講者）。
function computeEffectiveSpeaker(key) {
  if (!state.speakersMapping || state.speakersMapping.size === 0) return null;
  // sub-card key 是 "<parentIdx>:<partIdx>"；speaker sidecar 用 parent int key
  const parentIdx =
    typeof key === "string" && key.includes(":")
      ? Number(key.split(":", 1)[0])
      : Number(key);
  const v = state.speakersMapping.get(parentIdx);
  return typeof v === "string" && v.length > 0 ? v : null;
}

// 算這張卡實際生效的鏡頭：往前找最近一張 explicit 標過的卡，沒有就回 "a"
// 注意：carry-forward 是依「展開後」的順序，不是 idx 大小（idx 不一定連續、
// 而且切過的卡會 carry 到自己的後續 sub-card）。
function computeEffectiveCamera(key) {
  const rendered = expandedCards();
  const pos = rendered.findIndex((r) => r.key === key);
  if (pos < 0) return "a";
  for (let i = pos; i >= 0; i--) {
    const v = state.camerasMapping.get(rendered[i].key);
    if (v === "a" || v === "b") return v;
  }
  return "a";
}

// 整集 A/B 分布 ruler：依 expandedCards + carry-forward 染色，按時長比例算寬度
// 沒 cam B 時整條藏掉；hover 段落看時間範圍
function renderCamRuler() {
  const ruler = $("#cam-ruler");
  if (!ruler) return;
  const hasCamB = !!(state.cameras && state.cameras.b);
  if (!hasCamB || !state.cards.length) {
    ruler.hidden = true;
    ruler.innerHTML = "";
    return;
  }
  const rendered = expandedCards().filter((r) => !state.deletions.has(r.key));
  if (!rendered.length) {
    ruler.hidden = true;
    ruler.innerHTML = "";
    return;
  }
  const t0 = rendered[0].start;
  const t1 = rendered[rendered.length - 1].end;
  const total = Math.max(t1 - t0, 0.001);
  // 合併連續同色段：避免一張卡一塊 DOM，幾百張卡也只剩個位數段
  const segs = [];
  let curCam = null;
  let curStart = t0;
  let curEnd = t0;
  for (const r of rendered) {
    const cam = computeEffectiveCamera(r.key);
    if (cam === curCam) {
      curEnd = r.end;
    } else {
      if (curCam) segs.push({ cam: curCam, start: curStart, end: curEnd });
      curCam = cam;
      curStart = r.start;
      curEnd = r.end;
    }
  }
  if (curCam) segs.push({ cam: curCam, start: curStart, end: curEnd });
  ruler.innerHTML = "";
  for (const s of segs) {
    const seg = document.createElement("div");
    seg.className = `cam-ruler-seg cam-ruler-${s.cam}`;
    const w = ((s.end - s.start) / total) * 100;
    seg.style.width = `${w}%`;
    seg.title = `${s.cam.toUpperCase()} ｜ ${fmtTime(s.start)} – ${fmtTime(s.end)}（${(s.end - s.start).toFixed(1)}s）`;
    seg.addEventListener("click", () => {
      $("#video").currentTime = s.start;
    });
    ruler.appendChild(seg);
  }
  ruler.hidden = false;
}

// 分軌集講者分布 ruler：同 cam-ruler，但用 speakers sidecar 染色（每 speaker 一色）
// 跟 cam ruler 的差異：speaker 沒 carry-forward；沒掛 speaker 的段不畫（避免被誤解成「預設講者」）
function renderSpeakerRuler() {
  const ruler = $("#speaker-ruler");
  if (!ruler) return;
  const hasMics = state.mics && Object.keys(state.mics).length > 0;
  if (!hasMics || !state.cards.length) {
    ruler.hidden = true;
    ruler.innerHTML = "";
    return;
  }
  const rendered = expandedCards().filter((r) => !state.deletions.has(r.key));
  if (!rendered.length) {
    ruler.hidden = true;
    ruler.innerHTML = "";
    return;
  }
  const t0 = rendered[0].start;
  const t1 = rendered[rendered.length - 1].end;
  const total = Math.max(t1 - t0, 0.001);
  // 合併連續同 speaker 段；沒 speaker 的段（sidecar 缺漏）以 null 段保留位、用 .speaker-ruler-gap 染灰
  const segs = [];
  let curSp = "__init__";
  let curStart = t0;
  let curEnd = t0;
  for (const r of rendered) {
    const sp = computeEffectiveSpeaker(r.key);
    if (sp === curSp) {
      curEnd = r.end;
    } else {
      if (curSp !== "__init__") {
        segs.push({ sp: curSp, start: curStart, end: curEnd });
      }
      curSp = sp;
      curStart = r.start;
      curEnd = r.end;
    }
  }
  if (curSp !== "__init__")
    segs.push({ sp: curSp, start: curStart, end: curEnd });
  ruler.innerHTML = "";
  for (const s of segs) {
    const seg = document.createElement("div");
    seg.className = s.sp
      ? `speaker-ruler-seg speaker-${s.sp}`
      : "speaker-ruler-seg speaker-ruler-gap";
    const w = ((s.end - s.start) / total) * 100;
    seg.style.width = `${w}%`;
    const label = s.sp ? s.sp.toUpperCase() : "（無 speaker）";
    seg.title = `${label} ｜ ${fmtTime(s.start)} – ${fmtTime(s.end)}（${(s.end - s.start).toFixed(1)}s）`;
    seg.addEventListener("click", () => {
      $("#video").currentTime = s.start;
    });
    ruler.appendChild(seg);
  }
  ruler.hidden = false;
}

function renderCaption() {
  const overlay = $("#caption-overlay");
  const t = $("#video").currentTime;
  // 分軌啟用 → 找所有 active 卡分行（兩人同時講話 → 上下兩行 + speaker 著色）
  // 單軌集 → 退回單張卡的純文字（舊行為）
  const hasMics = state.mics && Object.keys(state.mics).length > 0;
  if (!hasMics) {
    const r = activeCardAt(t);
    if (!r || state.deletions.has(r.key)) {
      overlay.textContent = "";
      overlay.classList.remove("multi-speaker");
      return;
    }
    overlay.textContent = r.text;
    overlay.classList.remove("multi-speaker");
    return;
  }
  const rows = activeCardsAt(t).filter((r) => !state.deletions.has(r.key));
  if (rows.length === 0) {
    overlay.textContent = "";
    overlay.classList.remove("multi-speaker");
    return;
  }
  // 依 speaker key 字典序排（同 srt_merge），保證重疊時上下行順序穩定
  rows.sort((a, b) => {
    const sa = computeEffectiveSpeaker(a.key) || "";
    const sb = computeEffectiveSpeaker(b.key) || "";
    return sa.localeCompare(sb);
  });
  overlay.innerHTML = "";
  for (const r of rows) {
    const sp = computeEffectiveSpeaker(r.key);
    const line = document.createElement("div");
    line.className = "caption-line";
    if (sp) line.classList.add(`speaker-${sp}`);
    line.textContent = r.text;
    overlay.appendChild(line);
  }
  overlay.classList.toggle("multi-speaker", rows.length > 1);
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
  // T60：量測 renderCards 用時（搭配 .card 的 content-visibility）。
  // 若 cardCount > 500 且 dur > 50ms 就警告，當作導入 windowing 的訊號。
  const _t0 = performance.now();
  const list = $("#cards-list");
  list.innerHTML = "";
  if (state.needsTranscribe) {
    const empty = document.createElement("div");
    empty.className = "cards-empty";
    empty.innerHTML =
      '<div class="cards-empty-line">這一集還沒轉字幕</div>' +
      '<button type="button" class="btn btn-primary cards-empty-cta" id="cards-empty-cta">前往「檔案」轉字幕</button>';
    list.appendChild(empty);
    const cta = $("#cards-empty-cta");
    if (cta) {
      cta.addEventListener("click", () => {
        const drawer = $("#drawer");
        if (drawer) {
          drawer.classList.remove("collapsed");
          try {
            localStorage.setItem("edit.drawer.collapsed", "0");
          } catch (_) {}
        }
        const filesTab = $('[data-drawer-tab="files"]');
        if (filesTab) filesTab.click();
      });
    }
    return;
  }
  const hasCamB = !!state.cameras && !!state.cameras.b;
  const rendered = expandedCards();
  for (const r of rendered) {
    const c = r.c;
    const partIdx = r.partIdx; // null = 未切；0..N-1 = sub-card
    const key = r.key; // int (未切) 或 "<idx>:<part>"（切過）；deletions / cameras 都用這個
    const isSub = partIdx != null;
    const div = document.createElement("div");
    div.className = "card";
    div.dataset.idx = String(key);
    if (isSub) {
      div.classList.add("card-sub");
      const parts = state.cardSplits.get(c.idx) || [];
      if (partIdx === 0) {
        div.classList.add("card-sub-first");
        // 群組標頭：第一張 sub-card 上方掛「切自 #idx（原 X.Xs）」灰標
        // 讓讀者一眼看出這串卡是從哪張 STT 原句切出來的
        const origin = document.createElement("div");
        origin.className = "card-sub-origin";
        origin.textContent = `切自 #${c.idx}（原 ${(c.end - c.start).toFixed(1)}s ÷ ${parts.length} 段）`;
        list.appendChild(origin);
      }
      if (partIdx === parts.length - 1) div.classList.add("card-sub-last");
    }
    if (state.deletions.has(key)) div.classList.add("deleted");
    if (c.suspicious_pause && !isSub) div.classList.add("suspicious");
    // 雙機集：標記實際生效鏡頭，CSS 用 .card.cam-b 染左邊框
    if (hasCamB) {
      const eff = computeEffectiveCamera(key);
      div.classList.add(eff === "b" ? "cam-b" : "cam-a");
      div.classList.add("card-has-cam");
    }

    const susBox = document.createElement("input");
    susBox.type = "checkbox";
    susBox.className = "card-sus-check";
    // sub-card 不算可疑卡（可疑判定走原始整卡）；藏起來但保留版面位
    if (!c.suspicious_pause || isSub) susBox.classList.add("hidden");
    susBox.checked = !isSub && state.susChecked.has(c.idx);
    susBox.title =
      c.suspicious_pause && !isSub
        ? `可疑原因：${(c.suspicious_reasons || []).join(", ")}`
        : "";
    susBox.addEventListener("click", (e) => e.stopPropagation());
    susBox.addEventListener("change", () => {
      if (isSub) return;
      if (susBox.checked) {
        state.susChecked.add(c.idx);
      } else {
        state.susChecked.delete(c.idx);
      }
      renderSusToolbar();
    });

    const time = document.createElement("div");
    time.className = "card-time";
    time.textContent = `${fmtTime(r.start)}\n${fmtTime(r.end)}`;
    time.style.whiteSpace = "pre";
    time.addEventListener("click", () => {
      $("#video").currentTime = r.start;
    });
    // 分軌集才掛 speaker tag：值來自 srt_merge sidecar（read-only）
    // 要改 speaker → 走 _final_v2.speakers.json 手改或重跑 srt_merge，不在 UI 上 toggle
    const sp = computeEffectiveSpeaker(key);
    if (sp) {
      div.classList.add("card-has-speaker", `speaker-${sp}`);
      const tag = document.createElement("div");
      tag.className = `card-speaker-tag speaker-${sp}`;
      tag.textContent = sp.toUpperCase();
      tag.title = `講者：${sp.toUpperCase()}（來自分軌 SRT，要改去 sidecar）`;
      time.appendChild(tag);
    }
    // sub-card 加 duration 提示：直接看到「這段切了多少秒」+ 尾段空窗警告
    // 防的是放牛班式 bug：斷句配速太快導致大段時間沒分配字幕
    if (isSub) {
      const parts = state.cardSplits.get(c.idx) || [];
      const partDur = r.end - r.start;
      const durLine = document.createElement("div");
      durLine.className = "card-split-dur";
      durLine.textContent = `${partDur.toFixed(1)}s`;
      time.appendChild(durLine);
      // 配速進度：實際字數 / 這段時間最多能放幾字（以 SPLIT_SEC_PER_CHAR=0.3 為上限）
      // 超過 100% = 跟著字幕跑會喘；告訴使用者「這段切太短或字塞太多」
      const textLen = (parts[partIdx] || "").length;
      const maxChars = Math.max(Math.floor(partDur / SPLIT_SEC_PER_CHAR), 1);
      const pct = Math.round((textLen / maxChars) * 100);
      const pace = document.createElement("div");
      pace.className = "card-split-pace";
      // 預留 20% 緩衝再轉紅：理論上限 100% 不代表真的讀不完，>120% 才算明顯吃緊
      if (pct > 120) pace.classList.add("over");
      pace.textContent = `${textLen}/${maxChars} 字 · ${pct}%`;
      pace.title = `這段 ${partDur.toFixed(1)} 秒最多放 ${maxChars} 字（每字 ${SPLIT_SEC_PER_CHAR}s），實際 ${textLen} 字；>120% 才轉紅`;
      time.appendChild(pace);
      // 最後一段才檢查尾段空窗（tight-pack 模式下 partDur 之和會 < 原 cue dur）
      if (partIdx === parts.length - 1) {
        const trailing = c.end - r.end;
        if (trailing > 3) {
          const warn = document.createElement("div");
          warn.className = "card-split-warn";
          warn.textContent = `⚠ ${trailing.toFixed(1)}s`;
          warn.title = `斷句後尾段空窗 ${trailing.toFixed(1)} 秒沒分配字幕，可能漏字或斷句配速太快`;
          time.appendChild(warn);
        }
      }
    }

    const text = document.createElement("div");
    text.className = "card-text";
    text.contentEditable = "true";
    text.textContent = r.text;
    if (!isSub && state.textOverrides.has(c.idx)) text.classList.add("dirty");
    if (isSub) text.classList.add("dirty"); // sub-card 本來就是改過的內容
    text.addEventListener("blur", () => {
      const v = text.textContent.trim();
      if (isSub) {
        // 改 sub-card 文字 → 更新 cardSplits 對應 partIdx；空字串就還原原始 part 值（避免存空白卡）
        // Backspace 合併把 cardSplits[c.idx] 刪掉後，舊 sub-card DOM 被 re-render 摘掉會觸發 blur；
        // 此時不能再寫回 cardSplits，否則會把已合併的卡復活成有 undefined 段的拼接狀態。
        if (!state.cardSplits.has(c.idx)) return;
        const parts = state.cardSplits.get(c.idx).slice();
        if (parts.length <= partIdx) return;
        if (!parts[partIdx] && !v) return;
        if (parts[partIdx] === v) return;
        pushUndo();
        parts[partIdx] = v || parts[partIdx];
        state.cardSplits.set(c.idx, parts);
        renderTopbar();
        renderCaption();
        renderTypo();
        return;
      }
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
    // Enter：游標在文字中段 → 切成兩張子卡（sub-card 上可連鎖切）；在頭/尾 → 跳下一卡
    // Shift+Enter 保留原生換行 escape hatch
    // 注意 IME 組字中（如注音、拼音選字）不能攔 Enter，會吃掉候選確認
    text.addEventListener("keydown", (e) => {
      // Backspace at offset 0 on sub-card with partIdx > 0 → 把這段併回前一段
      // 修飾鍵不攔（讓 cmd+Backspace 刪整行還能用）；IME 組字中也不攔
      if (
        e.key === "Backspace" &&
        !e.shiftKey &&
        !e.metaKey &&
        !e.ctrlKey &&
        !e.altKey &&
        !e.isComposing &&
        isSub &&
        partIdx > 0 &&
        getCursorOffset(text) === 0
      ) {
        e.preventDefault();
        pushUndo();
        const oldParts = (state.cardSplits.get(c.idx) || []).slice();
        const leftText = oldParts[partIdx - 1] || "";
        const rightText = oldParts[partIdx] || "";
        const mergedText = leftText + rightText;
        const leftKey = `${c.idx}:${partIdx - 1}`;
        const thisKey = `${c.idx}:${partIdx}`;
        // 合併刪除狀態：只要其一被標刪，合併結果就是刪
        const mergedDeleted =
          state.deletions.has(leftKey) || state.deletions.has(thisKey);
        state.deletions.delete(thisKey);
        state.camerasMapping.delete(thisKey);
        // 後面的 sub-card composite key 從低往高 shift -1，避免覆寫
        for (let i = partIdx + 1; i < oldParts.length; i++) {
          const oldKey = `${c.idx}:${i}`;
          const newKey = `${c.idx}:${i - 1}`;
          if (state.deletions.has(oldKey)) {
            state.deletions.delete(oldKey);
            state.deletions.add(newKey);
          }
          if (state.camerasMapping.has(oldKey)) {
            const cam = state.camerasMapping.get(oldKey);
            state.camerasMapping.delete(oldKey);
            state.camerasMapping.set(newKey, cam);
          }
        }
        const newParts = oldParts
          .slice(0, partIdx - 1)
          .concat([mergedText], oldParts.slice(partIdx + 1));
        state.deletions.delete(leftKey);
        if (mergedDeleted) state.deletions.add(leftKey);
        if (newParts.length === 1) {
          // 只剩 1 段 → 收回未切狀態：composite "<idx>:0" 鍵搬回 int idx
          const finalText = newParts[0];
          state.cardSplits.delete(c.idx);
          if (state.deletions.has(`${c.idx}:0`)) {
            state.deletions.delete(`${c.idx}:0`);
            state.deletions.add(c.idx);
          }
          if (state.camerasMapping.has(`${c.idx}:0`)) {
            const cam = state.camerasMapping.get(`${c.idx}:0`);
            state.camerasMapping.delete(`${c.idx}:0`);
            state.camerasMapping.set(c.idx, cam);
          }
          if (finalText && finalText !== c.text) {
            state.textOverrides.set(c.idx, finalText);
          } else {
            state.textOverrides.delete(c.idx);
          }
          focusCardAt(c.idx, leftText.length);
        } else {
          state.cardSplits.set(c.idx, newParts);
          focusCardAt(`${c.idx}:${partIdx - 1}`, leftText.length);
        }
        return;
      }
      if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
      e.preventDefault();
      const cursorPos = getCursorOffset(text);
      const full = text.textContent;
      const before = full.slice(0, cursorPos).replace(/\s+$/, "");
      const after = full.slice(cursorPos).replace(/^\s+/, "");
      const canSplit =
        cursorPos > 0 && cursorPos < full.length && before && after;
      if (canSplit && !isSub) {
        // 未切的卡：第一次切，建立 2 段；int 鍵搬到 composite 避免存檔翻譯遺漏
        pushUndo();
        if (state.deletions.has(c.idx)) {
          state.deletions.delete(c.idx);
          state.deletions.add(`${c.idx}:0`);
          state.deletions.add(`${c.idx}:1`);
        }
        if (state.camerasMapping.has(c.idx)) {
          const cam = state.camerasMapping.get(c.idx);
          state.camerasMapping.delete(c.idx);
          state.camerasMapping.set(`${c.idx}:0`, cam);
          // 第二張靠 carry-forward 從第一張拿值，不顯式標
        }
        state.textOverrides.delete(c.idx); // splits 內容才是真相
        state.cardSplits.set(c.idx, [before, after]);
        focusSplitTarget(c.idx, 1);
        return;
      }
      if (canSplit && isSub) {
        // 已切過的 sub-card：把 parts[partIdx] 拆 left/right、後面 composite key +1
        // 同人同句不會跨 sub-card 換鏡頭，cam 走 carry-forward 不必額外處理
        pushUndo();
        // 先把 DOM 文字同步到新的左半，re-render 時舊元素的 blur handler 才不會
        // 拿原本的全段文字蓋回 parts[partIdx]（sub-card blur 會比對 parts[partIdx] vs v）
        text.textContent = before;
        const oldParts = (state.cardSplits.get(c.idx) || []).slice();
        const newParts = oldParts
          .slice(0, partIdx)
          .concat([before, after], oldParts.slice(partIdx + 1));
        // 後面的 sub-card composite key 從高往低 shift +1，避免覆寫
        for (let i = oldParts.length - 1; i > partIdx; i--) {
          const oldKey = `${c.idx}:${i}`;
          const newKey = `${c.idx}:${i + 1}`;
          if (state.deletions.has(oldKey)) {
            state.deletions.delete(oldKey);
            state.deletions.add(newKey);
          }
          if (state.camerasMapping.has(oldKey)) {
            const cam = state.camerasMapping.get(oldKey);
            state.camerasMapping.delete(oldKey);
            state.camerasMapping.set(newKey, cam);
          }
        }
        // 被切的子卡若是 deleted，右半繼承（這段音壞了 → 兩半都刪）
        if (state.deletions.has(`${c.idx}:${partIdx}`)) {
          state.deletions.add(`${c.idx}:${partIdx + 1}`);
        }
        state.cardSplits.set(c.idx, newParts);
        focusSplitTarget(c.idx, partIdx + 1);
        return;
      }
      // fallback：跳下一張可編輯卡
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
    del.setAttribute("aria-label", state.deletions.has(key) ? "復原" : "刪除");
    del.innerHTML = window.Icons
      ? window.Icons.get(state.deletions.has(key) ? "rotate-ccw" : "x", {
          size: 14,
        })
      : state.deletions.has(key)
        ? "↺"
        : "✕";
    del.addEventListener("click", () => {
      pushUndo();
      if (state.deletions.has(key)) {
        state.deletions.delete(key);
      } else {
        state.deletions.add(key);
      }
      renderCards();
      renderTopbar();
      renderCaption();
      renderTypo();
    });

    // 雙機集才有 A/B 膠囊；已刪除卡淡化但保留位置避免格線跳
    let camPill = null;
    if (hasCamB) {
      const eff = computeEffectiveCamera(key);
      camPill = document.createElement("div");
      camPill.className = "card-cam";
      if (state.deletions.has(key)) camPill.classList.add("muted");

      const aBtn = document.createElement("button");
      aBtn.type = "button";
      aBtn.className = "cam-btn cam-a-btn" + (eff === "a" ? " active" : "");
      aBtn.textContent = "A";
      aBtn.title = state.camerasMapping.get(key)
        ? "目前鏡頭（已 explicit 標記）"
        : "目前鏡頭（沿用前一張）";
      aBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        // 已經 explicit 標 a → 不入 stack 也不重畫
        if (state.camerasMapping.get(key) === "a") return;
        pushUndo();
        state.camerasMapping.set(key, "a");
        renderCards();
        // 暫停時 timeupdate 不會 fire，手動 refresh 一次 overlay 才會收掉
        refreshCamBOverlay();
      });

      const bBtn = document.createElement("button");
      bBtn.type = "button";
      bBtn.className = "cam-btn cam-b-btn" + (eff === "b" ? " active" : "");
      bBtn.textContent = "B";
      bBtn.title = state.camerasMapping.get(key)
        ? "切到 B 鏡頭（已 explicit 標記）"
        : "切到 B 鏡頭";
      bBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (state.camerasMapping.get(key) === "b") return;
        pushUndo();
        state.camerasMapping.set(key, "b");
        renderCards();
        // 暫停時也要立刻把 cam B overlay 疊上來
        refreshCamBOverlay();
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
  renderCamRuler();
  renderSpeakerRuler();
  // T60：把渲染數據塞到 dataset，方便 DevTools 直接看
  const _dur = performance.now() - _t0;
  list.dataset.lastRenderMs = _dur.toFixed(1);
  list.dataset.cardCount = String(rendered.length);
  if (rendered.length > 500 && _dur > 50) {
    console.warn(
      `[T60] renderCards 慢：${state.cards.length} 卡 / ${_dur.toFixed(1)}ms` +
        `（如果常態 > 50ms 就該導入 windowing）`,
    );
  }
}

// 紅卡 toolbar：總可疑數 / 已勾數 / 全選 / 刪除已勾
function renderSusToolbar() {
  const bar = $("#sus-toolbar");
  // 還沒刪除、也還沒切過的卡才算數（切過的卡 sus 旗標屬於原句長度，已不適用）
  const susCards = state.cards.filter(
    (c) =>
      c.suspicious_pause &&
      !state.deletions.has(c.idx) &&
      !state.cardSplits.has(c.idx),
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

  // 全選按鈕：全勾就顯示「取消全選」反之顯示「全選紅卡」（用 icon 區分）
  const allChecked = susCards.length > 0 && checkedCount === susCards.length;
  const iconName = allChecked ? "check-square" : "square";
  const label = allChecked ? "取消全選" : "全選紅卡";
  $("#sus-select-all").innerHTML = window.Icons
    ? `${window.Icons.get(iconName, { size: 14 })}<span>${label}</span>`
    : label;
}

async function loadEpisodeState() {
  // 只重抓 episode + cards，重新轉字幕後會用到
  const r = await fetch("/api/episode");
  if (r.status === 409) {
    // 後端尚未選集（重啟 / 多分頁 / 直接打 /edit URL）→ 回 dashboard 重選
    window.location.href = "/";
    throw new Error("尚未選集，導回 dashboard");
  }
  if (!r.ok) throw new Error(`/api/episode HTTP ${r.status}`);
  const data = await r.json();
  state.name = data.name;
  // crop_yt / crop_reels：拆 base + .b override 成兩個 state（前端編輯流好用）
  const ytIn = data.crop_yt || null;
  state.cropYt = ytIn
    ? { x: ytIn.x, y: ytIn.y, width: ytIn.width, height: ytIn.height }
    : null;
  state.cropYtB = ytIn && ytIn.b ? { ...ytIn.b } : null;
  const reelsIn = data.crop_reels || null;
  state.cropReels = reelsIn
    ? {
        x: reelsIn.x,
        y: reelsIn.y,
        width: reelsIn.width,
        height: reelsIn.height,
      }
    : null;
  state.cropReelsB = reelsIn && reelsIn.b ? { ...reelsIn.b } : null;
  state.deletions = new Set(data.deletions || []);
  state.cards = data.cards || [];
  state.textOverrides = new Map();
  state.susChecked = new Set();
  // 換集 / 重轉字幕：清掉舊集的切分記錄，避免 idx 對到新集不存在的卡或文字不符
  state.cardSplits = new Map();
  state.needsTranscribe = !!data.needs_transcribe;
  state.hasMainVideo = data.has_main_video !== false;
  state.headTrimSec = Number(data.head_trim_sec) || 0;
  state.tailTrimSec = Number(data.tail_trim_sec) || 0;
  // Reels 片段：來自 episode.yaml；list of {name, start_card, end_card}
  state.reelsClips = Array.isArray(data.reels_clips)
    ? data.reels_clips
        .filter(
          (c) => c && typeof c.name === "string" && c.start_card && c.end_card,
        )
        .map((c) => ({
          name: String(c.name),
          start_card: Number(c.start_card),
          end_card: Number(c.end_card),
        }))
    : [];
  // 雙鏡頭 mapping：API 回傳 key 是字串（JSON 不支援 int key），這裡轉回 Number
  state.cameras = data.cameras || {};
  state.camerasMapping = new Map(
    Object.entries(data.cameras_mapping || {})
      .map(([k, v]) => [Number(k), v])
      .filter(([_, v]) => v === "a" || v === "b"),
  );
  // 分軌 speaker mapping：mics 同 cameras 形狀；speaker 不做 carry-forward（每張卡都明確標記）
  // 合法 speaker = mics 的 key set；mics 為空（單軌集）→ 不收任何 mapping
  state.mics = data.mics || {};
  const validSpeakers = new Set(Object.keys(state.mics));
  state.speakersMapping = new Map(
    Object.entries(data.speakers_mapping || {})
      .map(([k, v]) => [Number(k), v])
      .filter(([_, v]) => typeof v === "string" && validSpeakers.has(v)),
  );
  // T23a-followup：cam B 候選 + 同步 offset（給 modal 用）
  state.camBCandidates = Array.isArray(data.cam_b_candidates)
    ? data.cam_b_candidates
    : [];
  state.camSyncOffsetB = Number((data.camera_sync_offset || {}).b || 0);
  // 外接音檔：候選 + 已存的 path / sync_offset
  state.audioCandidates = Array.isArray(data.audio_candidates)
    ? data.audio_candidates
    : [];
  state.audioPath = (data.audio && data.audio.path) || "";
  state.audioSyncOffset = Number((data.audio || {}).sync_offset || 0);
  // 「最終合成總覽」：cam A 候選 / 目前 cam A / 字幕檔（read-only）
  state.camACandidates = Array.isArray(data.cam_a_candidates)
    ? data.cam_a_candidates
    : [];
  state.camAPath = data.cam_a_path || "";
  state.srtPath = data.srt_path || "";
  state.srtCandidates = Array.isArray(data.srt_candidates)
    ? data.srt_candidates
    : [];
  // 字幕風格 + 輸出解析度：給 caption preview 用，讓預覽字體跟 ffmpeg 輸出等比
  state.subtitleStyleYt = data.subtitle_style || null;
  state.subtitleStyleReels =
    data.subtitle_style_reels || data.subtitle_style || null;
  const parseRes = (s) => {
    const [w, h] = String(s || "")
      .split("x")
      .map(Number);
    return Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0
      ? { w, h }
      : null;
  };
  state.outputResYt = parseRes(data.output_resolution_yt) || {
    w: 1920,
    h: 1080,
  };
  state.outputResReels = parseRes(data.output_resolution_reels) || {
    w: 1080,
    h: 1920,
  };
  // 字幕時間軸對齊：原始字幕是 cam A 時間軸；外接音檔比 cam A 慢 sync_offset 秒
  // → 字幕 start/end 都要往前推 -audioSyncOffset，讓字幕顯示時機跟外接音檔同步
  if (state.audioPath && state.audioSyncOffset) {
    const shift = -state.audioSyncOffset;
    state.cards = state.cards
      .map((c) => ({
        ...c,
        start: Math.max(0, (c.start || 0) + shift),
        end: (c.end || 0) + shift,
      }))
      .filter((c) => c.end > 0);
  }
  // 換集 / 重抓 episode → 既有的 undo 紀錄不再有意義（idx 範圍可能不同）
  clearUndoStacks();
  applyMainVideoMissingUI();
}

// 空集（01_母帶/ 沒檔，main_video 解析後不存在）→ 把 video 換成 empty banner，
// 並把 <video src> 清掉避免無謂的 /api/video 404 request。
function applyMainVideoMissingUI() {
  const banner = document.getElementById("video-missing");
  const wrap = document.querySelector(".video-wrap");
  const v = document.getElementById("video");
  if (!banner || !wrap || !v) return;
  if (state.hasMainVideo) {
    banner.hidden = true;
    wrap.classList.remove("has-missing");
    if (!v.getAttribute("src")) {
      v.src = "/api/video";
      v.load();
    }
  } else {
    banner.hidden = false;
    wrap.classList.add("has-missing");
    if (v.getAttribute("src")) {
      v.removeAttribute("src");
      v.load();
    }
    if (window.Icons) window.Icons.inject(banner);
  }
}

function setupSusToolbar() {
  $("#sus-select-all").addEventListener("click", () => {
    const susCards = state.cards.filter(
      (c) =>
        c.suspicious_pause &&
        !state.deletions.has(c.idx) &&
        !state.cardSplits.has(c.idx),
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
    if (!r.ok) {
      console.warn(
        "[loadFiles] HTTP",
        r.status,
        await r.text().catch(() => ""),
      );
      state.files = [];
      return;
    }
    const data = await r.json();
    state.files = data.files || [];
  } catch (e) {
    console.error("[loadFiles] failed:", e);
    state.files = [];
  }
}

async function loadConfig() {
  try {
    const r = await fetch("/api/config");
    if (!r.ok) return;
    const data = await r.json();
    state.hasGeminiKey = !!data.has_gemini_api_key;
    state.hasOpenAIKey = !!data.has_openai_api_key;
    // xai 已下架；舊 config 殘留 "xai" 一律當 gemini
    state.sttProvider = ["openai", "whisper_mlx"].includes(data.provider)
      ? data.provider
      : "gemini";
    state.assetsStatus = data.assets || {};
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
  renderReelsClips();
  setupExternalAudio();
  setupCamBOverlay();
  resumeTranscribeIfRunning();
}

// === 錯字表 ===

// 取得卡片「當前文字」（含 textOverrides）並排除已刪除卡
// 切過的卡 → 回 null 跳過：split 後文字屬於使用者手動編輯範圍，
// 全域字典批次替換不應該動到，避免覆蓋手切過的內容。
function currentCardText(c) {
  if (state.deletions.has(c.idx)) return null;
  if (state.cardSplits.has(c.idx)) return null;
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
  const tabCount = $("#drawer-count-typo");
  if (tabCount) {
    const n = state.typoDict.length + overrides.length;
    tabCount.textContent = n > 0 ? String(n) : "";
  }
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

// 收集排序好的「刪除時間區間」— 用於預覽時跳過，讓畫面跟最終輸出一致。
// 相鄰區間（gap < 0.05s）合併，避免 seek 完馬上又被踢一次。
function deletionIntervals() {
  const raw = [];
  for (const r of expandedCards()) {
    if (state.deletions.has(r.key)) raw.push([r.start, r.end]);
  }
  raw.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const [s, e] of raw) {
    const last = merged[merged.length - 1];
    if (last && s <= last[1] + 0.05) last[1] = Math.max(last[1], e);
    else merged.push([s, e]);
  }
  return merged;
}

// 把 t 算到下一個 keep 區間的起點：在 deleted 區間內 → 跳到區間末端；
// 不在則回 t 本身。用於 play / timeupdate 時把預覽對齊到最終輸出時間軸。
function nextKeepTime(t) {
  for (const [s, e] of deletionIntervals()) {
    if (t >= s && t < e) return e;
  }
  return t;
}

// 播放中若 currentTime 進入刪除區間 → 直接跳到區間末端，跟最終輸出體感一致。
// 暫停 / 拖 seek 時不踢，讓使用者還能進到刪除卡裡 inspect / 反悔復原。
function autoSkipDeletedSegments() {
  const v = $("#video");
  if (v.paused) return;
  const jumped = nextKeepTime(v.currentTime);
  if (jumped > v.currentTime + 0.01) v.currentTime = jumped;
}

// 上一次 highlight 的 card key — 只有真的換卡才動 .playing class 和 scrollIntoView，
// 避免 timeupdate（4-30Hz）一直疊 smooth scroll 動畫互打架造成卡片列表「亂跳」。
let _lastActiveKey = null;
$("#video").addEventListener("timeupdate", () => {
  autoPauseAtTailTrim();
  autoSkipDeletedSegments();
  const t = $("#video").currentTime;
  const dur = $("#video").duration;
  $("#time").textContent = `${fmtTime(t)} / ${fmtTime(dur)}`;
  $("#seek").value = dur ? (t / dur) * 100 : 0;

  const activeCard = activeCardAt(t);
  const activeKey = activeCard ? String(activeCard.key) : null;
  if (activeKey !== _lastActiveKey) {
    document
      .querySelectorAll(".card.playing")
      .forEach((el) => el.classList.remove("playing"));
    if (activeKey != null) {
      const el = document.querySelector(`.card[data-idx="${activeKey}"]`);
      if (el) {
        el.classList.add("playing");
        el.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }
    _lastActiveKey = activeKey;
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
function setPlayIcon(name) {
  playBtn.innerHTML = window.Icons
    ? window.Icons.get(name, { size: 16 })
    : name === "pause"
      ? "⏸"
      : "▶";
  playBtn.setAttribute("aria-label", name === "pause" ? "暫停" : "播放");
}
$("#video").addEventListener("play", () => {
  setPlayIcon("pause");
  // C3：按 play 時若卡在 head trim 區 → 自動跳到 headTrim 邊界
  const v = $("#video");
  const head = state.headTrimSec || 0;
  if (head > 0 && v.currentTime < head) v.currentTime = head;
  // 按 play 時若停在刪除卡上 → 直接跳到區間末端，跟最終輸出一致
  const jumped = nextKeepTime(v.currentTime);
  if (jumped > v.currentTime + 0.01) v.currentTime = jumped;
});
$("#video").addEventListener("pause", () => {
  setPlayIcon("play");
});

$("#seek").addEventListener("input", (e) => {
  const v = $("#video");
  if (v.duration) v.currentTime = (e.target.value / 100) * v.duration;
});

// 影片載入完才能算頭尾 trim 在 seek 上的百分比，所以這裡也要重畫
$("#video").addEventListener("loadedmetadata", () => {
  renderTrimControls();
});

// === 外接音檔預覽綁定 ===
// 有 audio.path → 用外接音檔的聲音覆蓋影片原音、保持時間軸鏡像
// 沒有 → 還原影片原音、解綁所有事件
function setupExternalAudio() {
  const video = $("#video");
  const audio = $("#external-audio");
  if (!audio) return;
  // 每次重綁前先卸載舊 listeners，避免換集後多次累積
  if (window.__audioMirror) {
    for (const [ev, fn] of window.__audioMirror) {
      video.removeEventListener(ev, fn);
    }
    window.__audioMirror = null;
  }
  if (!state.audioPath) {
    video.muted = false;
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    return;
  }
  // cache-bust：換集後同檔名也要重抓
  audio.src = `/api/audio?path=${encodeURIComponent(state.audioPath)}&_=${Date.now()}`;
  audio.load();
  video.muted = true;
  const offset = state.audioSyncOffset || 0;
  // 影片在 cam A 時間軸；audio 的同一個物理瞬間 = video.currentTime + sync_offset
  const sync = () => {
    const target = Math.max(0, video.currentTime + offset);
    if (Math.abs(audio.currentTime - target) > 0.05) {
      audio.currentTime = target;
    }
  };
  const onPlay = () => {
    sync();
    audio.play().catch(() => {});
  };
  const onPause = () => audio.pause();
  const onSeek = () => sync();
  const onRate = () => {
    audio.playbackRate = video.playbackRate;
  };
  const onVol = () => {
    audio.volume = video.volume;
    // video.muted 永遠維持 true（外接音檔在播），不要被 UI 一鍵切回
  };
  const onLoadedAudio = () => sync();
  video.addEventListener("play", onPlay);
  video.addEventListener("pause", onPause);
  video.addEventListener("seeking", onSeek);
  video.addEventListener("seeked", onSeek);
  video.addEventListener("ratechange", onRate);
  video.addEventListener("volumechange", onVol);
  audio.addEventListener("loadedmetadata", onLoadedAudio, { once: true });
  window.__audioMirror = [
    ["play", onPlay],
    ["pause", onPause],
    ["seeking", onSeek],
    ["seeked", onSeek],
    ["ratechange", onRate],
    ["volumechange", onVol],
  ];
}

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

// 拖曳 trim handle：mousedown 在拖把上 → mousemove 即時更新 sec → mouseup 收工。
// pushUndo 只在第一次 move 時押一次，避免拖一下噴一堆 history。
// 點下去沒拖 = 沒進 stack，跟 frame drag / resize 同 pattern。
function startTrimDrag(kind) {
  const v = $("#video");
  const dur = v.duration || 0;
  if (!dur) return;
  const handle =
    kind === "head" ? $("#trim-handle-head") : $("#trim-handle-tail");
  handle.classList.add("dragging");
  const wrap = $(".seek-wrap");
  const rect = wrap.getBoundingClientRect();
  let pushed = false;

  const onMove = (e) => {
    const x = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
    const sec = (x / rect.width) * dur;
    // clamp：頭不能超過尾的對面；尾同理；最少留 0.5s 內容免得整段被吃光
    const MIN_REMAIN = 0.5;
    if (kind === "head") {
      const maxHead = dur - (state.tailTrimSec || 0) - MIN_REMAIN;
      const next = Math.round(Math.max(0, Math.min(sec, maxHead)) * 10) / 10;
      if (next === state.headTrimSec) return;
      if (!pushed) {
        pushUndo();
        pushed = true;
      }
      state.headTrimSec = next;
    } else {
      const tailFromEnd = dur - sec;
      const maxTail = dur - (state.headTrimSec || 0) - MIN_REMAIN;
      const next =
        Math.round(Math.max(0, Math.min(tailFromEnd, maxTail)) * 10) / 10;
      if (next === state.tailTrimSec) return;
      if (!pushed) {
        pushUndo();
        pushed = true;
      }
      state.tailTrimSec = next;
    }
    renderTrimControls();
    renderTopbar();
  };
  const onUp = () => {
    handle.classList.remove("dragging");
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

$("#trim-handle-head").addEventListener("mousedown", (e) => {
  e.preventDefault();
  startTrimDrag("head");
});
$("#trim-handle-tail").addEventListener("mousedown", (e) => {
  e.preventDefault();
  startTrimDrag("tail");
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
  // 409 → loadEpisodeState 已觸發 window.location.href，不要 flash 錯誤畫面
  if (err?.message?.includes("尚未選集")) return;
  $("#title").textContent = "載入失敗";
  $("#status").textContent = `載入失敗：${err?.message || err}`;
  console.error(err);
});

initUploadDropZone();
setupDrawer();

// === Drawer：底部「專案檔案 / 字典」分頁 + 收合（localStorage 持久化） ===
function setupDrawer() {
  const drawer = $("#drawer");
  if (!drawer) return;
  const tabs = drawer.querySelectorAll(".drawer-tab");
  const panes = drawer.querySelectorAll(".drawer-pane");
  const toggle = $("#drawer-toggle");

  const KEY_TAB = "edit.drawer.tab";
  const KEY_COLLAPSED = "edit.drawer.collapsed";

  const showTab = (name) => {
    tabs.forEach((t) => {
      const active = t.dataset.drawerTab === name;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
      // WAI-ARIA roving tabindex：只有 active tab 進 Tab 序列
      t.setAttribute("tabindex", active ? "0" : "-1");
    });
    panes.forEach((p) => {
      p.hidden = p.dataset.drawerPane !== name;
    });
    try {
      localStorage.setItem(KEY_TAB, name);
    } catch (_) {}
  };

  const expandIfCollapsed = () => {
    if (drawer.classList.contains("collapsed")) {
      drawer.classList.remove("collapsed");
      try {
        localStorage.setItem(KEY_COLLAPSED, "0");
      } catch (_) {}
    }
  };

  tabs.forEach((t, idx) => {
    t.addEventListener("click", () => {
      expandIfCollapsed();
      showTab(t.dataset.drawerTab);
    });
    // ArrowLeft/Right 切上下 tab + focus；Home/End 跳第一/最後
    t.addEventListener("keydown", (e) => {
      let next = -1;
      if (e.key === "ArrowRight") next = (idx + 1) % tabs.length;
      else if (e.key === "ArrowLeft")
        next = (idx - 1 + tabs.length) % tabs.length;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = tabs.length - 1;
      else return;
      e.preventDefault();
      const nextName = tabs[next].dataset.drawerTab;
      expandIfCollapsed();
      showTab(nextName);
      tabs[next].focus();
    });
  });

  if (toggle) {
    toggle.addEventListener("click", () => {
      const collapsed = drawer.classList.toggle("collapsed");
      try {
        localStorage.setItem(KEY_COLLAPSED, collapsed ? "1" : "0");
      } catch (_) {}
    });
  }

  // 還原上次狀態
  try {
    const savedTab = localStorage.getItem(KEY_TAB);
    if (savedTab) showTab(savedTab);
    if (localStorage.getItem(KEY_COLLAPSED) === "1") {
      drawer.classList.add("collapsed");
    }
  } catch (_) {}
}

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
    // ratio 是 per-version 共享（輸出尺寸固定 → 兩鏡頭 aspect 必須一致）
    // 所以一律重設 base；B override 若已存在也同步到新 ratio
    const newCrop = cropForRatio(ratioStr);
    if (state.activeVersion === "yt") {
      state.cropYt = { ...newCrop };
      if (state.cropYtB) state.cropYtB = { ...newCrop };
    } else {
      state.cropReels = { ...newCrop };
      if (state.cropReelsB) state.cropReelsB = { ...newCrop };
    }
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
    // 編 B 且有 B override → 只清 B（回到沿用 A）
    if (getActiveCropCam() === "b" && _bCrop()) {
      pushUndo();
      if (state.activeVersion === "yt") state.cropYtB = null;
      else state.cropReelsB = null;
      renderCropInfo();
      updateRatioButtons();
      return;
    }
    // 否則整版本全清（base + B override + ratio）
    if (getActiveCrop() == null && getActiveCropRatio() == null) return;
    pushUndo();
    if (state.activeVersion === "yt") {
      state.cropYt = null;
      state.cropYtB = null;
    } else {
      state.cropReels = null;
      state.cropReelsB = null;
    }
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
      renderReelsClips();
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

// === Reels 片段：sub-panel 渲染 + 表單/匯出 handler ===
// 只在 activeVersion === "reels" 顯示；後端 /api/clip 從已合成的 Reels mp4 -c copy 切片
function renderReelsClips() {
  const panel = $("#reels-clips-panel");
  if (!panel) return;
  const isReels = state.activeVersion === "reels";
  panel.classList.toggle("hidden", !isReels);
  if (!isReels) return;

  const list = $("#reels-clips-list");
  const count = $("#reels-clips-count");
  const exportBtn = $("#reels-clip-export-btn");
  const clips = state.reelsClips || [];
  count.textContent = String(clips.length);
  exportBtn.disabled = clips.length === 0;

  list.innerHTML = "";
  if (clips.length === 0) {
    const empty = document.createElement("div");
    empty.className = "reels-clips-empty";
    empty.textContent = "尚未加片段。下面輸入名稱 + 起卡 # + 迄卡 #。";
    list.appendChild(empty);
    return;
  }
  clips.forEach((clip, idx) => {
    const row = document.createElement("div");
    row.className = "reels-clip-item";
    const label = document.createElement("span");
    label.textContent = `${clip.name}`;
    const range = document.createElement("span");
    range.textContent = `#${clip.start_card}-${clip.end_card}`;
    range.className = "reels-clip-range";
    const del = document.createElement("button");
    del.type = "button";
    del.title = "刪除這段";
    del.innerHTML = window.Icons
      ? window.Icons.get("trash-2", { size: 14 })
      : "×";
    del.addEventListener("click", () => {
      pushUndo();
      state.reelsClips.splice(idx, 1);
      renderReelsClips();
    });
    row.appendChild(label);
    row.appendChild(range);
    row.appendChild(del);
    list.appendChild(row);
  });
}

function setReelsClipStatus(text, tone) {
  const el = $("#reels-clip-status");
  if (!el) return;
  el.textContent = text || "";
  el.classList.remove("tone-success", "tone-danger");
  if (tone) el.classList.add(`tone-${tone}`);
}

function setupReelsClips() {
  const form = $("#reels-clip-form");
  const nameInput = $("#reels-clip-name");
  const startInput = $("#reels-clip-start");
  const endInput = $("#reels-clip-end");
  const exportBtn = $("#reels-clip-export-btn");
  if (!form || !exportBtn) return;

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const name = nameInput.value.trim();
    const startCard = Number(startInput.value);
    const endCard = Number(endInput.value);
    if (!name) {
      setReelsClipStatus("片段名不能空", "danger");
      return;
    }
    if (!Number.isInteger(startCard) || !Number.isInteger(endCard)) {
      setReelsClipStatus("起卡 / 迄卡要是整數", "danger");
      return;
    }
    if (startCard > endCard) {
      setReelsClipStatus("起卡 # 不能大於迄卡 #", "danger");
      return;
    }
    const idxSet = new Set(state.cards.map((c) => c.idx));
    if (!idxSet.has(startCard) || !idxSet.has(endCard)) {
      setReelsClipStatus(
        `卡 #${startCard} 或 #${endCard} 不存在（或已被刪除）`,
        "danger",
      );
      return;
    }
    if (state.reelsClips.some((c) => c.name === name)) {
      setReelsClipStatus(`片段名「${name}」重複`, "danger");
      return;
    }
    pushUndo();
    state.reelsClips.push({
      name,
      start_card: startCard,
      end_card: endCard,
    });
    nameInput.value = "";
    startInput.value = "";
    endInput.value = "";
    setReelsClipStatus(`已加「${name}」（記得按完成並儲存）`, "success");
    renderReelsClips();
  });

  exportBtn.addEventListener("click", async () => {
    const clips = state.reelsClips || [];
    if (clips.length === 0) return;
    exportBtn.disabled = true;
    const originalLabel = exportBtn.innerHTML;
    exportBtn.innerHTML = window.Icons
      ? `${window.Icons.get("loader", { size: 14 })}<span>切片中…</span>`
      : "切片中…";
    setReelsClipStatus("正在切片（每段 ffmpeg -c copy 約 1-3 秒）…", null);
    try {
      const r = await fetch("/api/clip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force: true }),
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        throw new Error(data.error || `HTTP ${r.status}`);
      }
      const outClips = data.clips || [];
      const summary = outClips
        .map((c) => `${c.name} (${c.duration.toFixed(1)}s)`)
        .join(" / ");
      setReelsClipStatus(
        `✓ 已輸出 ${outClips.length} 段：${summary}`,
        "success",
      );
    } catch (err) {
      setReelsClipStatus(`✗ 切片失敗：${err.message}`, "danger");
    } finally {
      exportBtn.innerHTML = originalLabel;
      exportBtn.disabled = state.reelsClips.length === 0;
    }
  });
}

// === 儲存 / 取消 ===
// 所有 /api/save 共用的序列化通道：主儲存鈕、cam modal 儲存、一鍵對齊 auto-save
// 三條路徑可能並發；不序列化的話兩個 POST 交錯，後發先回會互蓋 episode.yaml。
let _saveChain = Promise.resolve();
function postSave(payload) {
  const run = async () => {
    const r = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r;
  };
  // 不管前一發成敗都接著跑；回傳的 promise 保留各呼叫端自己的錯誤處理
  const p = _saveChain.then(run, run);
  _saveChain = p.catch(() => {});
  return p;
}

function setSaveBtnLabel(iconName, text) {
  const btn = $("#save-btn");
  btn.innerHTML = window.Icons
    ? `${window.Icons.get(iconName, { size: 16 })}<span>${text}</span>`
    : text;
}

// 任意按鈕：icon + 文字。傳 null 給 iconName 表示純文字（loading 狀態用）。
function setBtnLabel(btn, iconName, text) {
  if (!btn) return;
  if (iconName && window.Icons) {
    btn.innerHTML = `${window.Icons.get(iconName, { size: 14 })}<span>${text}</span>`;
  } else {
    btn.textContent = text;
  }
}

// 把按鈕切到 loading 狀態：左側 spinner + 「<label>… mm:ss」每秒跳動。
// 用於自動對齊這類 30 秒到 3 分鐘的 ffmpeg + correlate 流程，避免使用者誤判卡住。
// 回傳 stop()；呼叫端在最終狀態之前先 stop()，再用 setBtnLabel 接續顯示。
function startBtnSpinner(btn, label = "計算中") {
  if (!btn) return () => {};
  const t0 = performance.now();
  const render = () => {
    const sec = Math.floor((performance.now() - t0) / 1000);
    const mm = Math.floor(sec / 60);
    const ss = String(sec % 60).padStart(2, "0");
    btn.innerHTML = `<span class="spinner"></span><span>${label}… ${mm}:${ss}</span>`;
  };
  render();
  const timer = setInterval(render, 1000);
  return () => clearInterval(timer);
}

// 統一 modal 標題：icon + 文字 + 狀態色（success / danger / warning / accent）
function setModalStatusTitle(elId, iconName, text, tone = "") {
  const el = document.getElementById(elId);
  if (!el) return;
  const ico = window.Icons ? window.Icons.get(iconName, { size: 16 }) : "";
  el.innerHTML = `${ico}<span>${text}</span>`;
  el.classList.remove(
    "tone-success",
    "tone-danger",
    "tone-warning",
    "tone-accent",
  );
  if (tone) el.classList.add("tone-" + tone);
}

// init modal 的檔案列表用：icon + 檔名
function _setInitRow(row, iconName, label) {
  const ico = window.Icons ? window.Icons.get(iconName, { size: 14 }) : "";
  row.innerHTML = `${ico}<span>${label}</span>`;
}
$("#save-btn").addEventListener("click", async () => {
  $("#save-btn").disabled = true;
  setSaveBtnLabel("save", "儲存中…");
  const payload = {
    crop_yt: serializeCropForSave(state.cropYt, state.cropYtB),
    crop_reels: serializeCropForSave(state.cropReels, state.cropReelsB),
    // deletions / cameras_mapping key 可能是 int（未切卡）或 "<idx>:<part>"（子卡）→ 不能用 int sort
    deletions: [...state.deletions],
    head_trim_sec: state.headTrimSec,
    tail_trim_sec: state.tailTrimSec,
    cards: [...state.textOverrides.entries()].map(([idx, text]) => ({
      idx,
      text,
    })),
    // 只送 explicit 標記，carry-forward 推算結果不送；後端會 _parse_composite_id 解 "5:1" 或 5
    cameras_mapping: Object.fromEntries(state.camerasMapping),
    // 分軌 speaker mapping：同 cameras_mapping 形狀；後端會用 mics keys 驗證 + composite id 翻譯
    speakers_mapping: Object.fromEntries(state.speakersMapping),
    // 切卡：{ "<old_idx>": ["前段", "後段", ...] }；後端按文字長度比例分配時間 + 重編號
    splits: Object.fromEntries(state.cardSplits),
    // Reels 片段：list of {name, start_card, end_card}；空 list 後端會把 key 砍掉
    reels_clips: state.reelsClips.map((c) => ({
      name: c.name,
      start_card: c.start_card,
      end_card: c.end_card,
    })),
  };
  try {
    await postSave(payload);
    setSaveBtnLabel("check", "已儲存");
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
      setSaveBtnLabel("check", "完成並儲存");
      $("#save-btn").disabled = false;
    }, 2000);
  } catch (e) {
    alert(`儲存失敗：${e.message}`);
    $("#save-btn").disabled = false;
    setSaveBtnLabel("check", "完成並儲存");
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
  { kind: "main_video", label: "主影片", icon: "film" },
  { kind: "subtitle", label: "字幕", icon: "file-text" },
  { kind: "composite", label: "合成輸出", icon: "package" },
  { kind: "master", label: "母帶", icon: "mic" },
  { kind: "work", label: "工作檔", icon: "wrench" },
  { kind: "other", label: "其他", icon: "file" },
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

  const iconHtml = (name, size = 12) =>
    window.Icons ? window.Icons.get(name, { size }) : "";

  let preview;
  if (f.previewable) {
    preview = document.createElement("button");
    preview.className = "file-preview" + (isActive ? " active" : "");
    const eyeIcon = iconHtml(isActive ? "eye" : "eye-off", 12);
    preview.innerHTML = `${eyeIcon}<span>${isActive ? "預覽中" : "預覽"}</span>`;
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
    action.innerHTML = `${iconHtml("mic", 12)}<span>轉字幕</span>`;
    const providerLabel = providerLabelOf(state.sttProvider);
    const hasSelectedKey = hasKeyForProvider(state.sttProvider);
    action.title = hasSelectedKey
      ? `用 ${providerLabel} STT 轉字幕並覆蓋 _v2.srt`
      : `請先到設定面板填 ${providerLabel} API key`;
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
  if (summary) {
    summary.textContent = `${total} 個檔案 · ${audio} 個可轉字幕 · 預覽中：${previewLabel}`;
  }
  const tabCount = $("#drawer-count-files");
  if (tabCount) tabCount.textContent = total > 0 ? String(total) : "";

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
    const caretIcon = window.Icons
      ? window.Icons.get(isCollapsed ? "chevron-right" : "chevron-down", {
          size: 12,
        })
      : isCollapsed
        ? "▶"
        : "▼";
    const sectionIcon = window.Icons
      ? window.Icons.get(section.icon, { size: 14 })
      : "";
    header.innerHTML = `
      <span class="caret">${caretIcon}</span>
      <span class="section-icon">${sectionIcon}</span>
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
  // 預覽非主影片時暫時關掉外接音檔鏡像；切回主影片時重綁
  if (state.previewPath) {
    const audio = $("#external-audio");
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    video.muted = false;
    if (window.__audioMirror) {
      for (const [ev, fn] of window.__audioMirror) {
        video.removeEventListener(ev, fn);
      }
      window.__audioMirror = null;
    }
  } else {
    setupExternalAudio();
  }
}

// === 播放時跟著 cameras_mapping 切預覽（用兩個 video 疊加 + visibility toggle）===
// 主 #video 永遠播 cam A，#video-camb 疊上去 mirror 播 cam B；卡片邊界切時只 flip visibility。
// 好處：無黑畫面、字幕時軸永遠以 cam A 為主所以不會偏、seek 精度不受影響。
// 成本：cam B 一直在背景 decode，記憶體 / CPU 多一份。
function setupCamBOverlay() {
  const main = $("#video");
  const camb = $("#video-camb");
  if (!camb) return;
  // 卸舊 mirror
  if (window.__camBMirror) {
    for (const [ev, fn] of window.__camBMirror) {
      main.removeEventListener(ev, fn);
    }
    window.__camBMirror = null;
  }
  camb.classList.remove("active");
  camb.pause();

  const camBPath = state.cameras && state.cameras.b;
  if (!camBPath) {
    camb.removeAttribute("src");
    camb.load();
    return;
  }

  camb.src = `/api/video?path=${encodeURIComponent(camBPath)}&_=${Date.now()}`;
  camb.muted = true;
  camb.load();

  const offset = state.camSyncOffsetB || 0;
  // assemble.py 用 setpts=PTS-{sync_offset_b}/TB → camB.currentTime = main.currentTime + offset
  const sync = () => {
    const target = Math.max(0, main.currentTime + offset);
    if (Math.abs(camb.currentTime - target) > 0.05) {
      camb.currentTime = target;
    }
  };
  const onPlay = () => {
    sync();
    if (camb.classList.contains("active")) camb.play().catch(() => {});
  };
  const onPause = () => camb.pause();
  const onSeek = () => sync();
  const onRate = () => {
    camb.playbackRate = main.playbackRate;
  };
  main.addEventListener("play", onPlay);
  main.addEventListener("pause", onPause);
  main.addEventListener("seeking", onSeek);
  main.addEventListener("seeked", onSeek);
  main.addEventListener("ratechange", onRate);
  window.__camBMirror = [
    ["play", onPlay],
    ["pause", onPause],
    ["seeking", onSeek],
    ["seeked", onSeek],
    ["ratechange", onRate],
  ];
  // 載入後若當下卡片就標 B，立刻 overlay；不等到 user hit play / seek
  refreshCamBOverlay();
}

// 抽出共用切換邏輯，給 timeupdate（播放中）和 A/B 按鈕點擊（暫停時）共用
// 不靠 timeupdate fire 才生效，按鈕一按就反映在 overlay 上
function refreshCamBOverlay() {
  const main = $("#video");
  const camb = $("#video-camb");
  if (!camb) return;
  const camBPath = state.cameras && state.cameras.b;
  // 沒 cam B / 使用者手動切到別的預覽（switchPreview）→ 收 overlay
  if (!camBPath || state.previewPath !== null) {
    if (camb.classList.contains("active")) {
      camb.classList.remove("active");
      camb.pause();
    }
    return;
  }
  const card = activeCardAt(main.currentTime);
  if (!card) return;
  const eff = computeEffectiveCamera(card.key);
  const shouldBeActive = eff === "b";
  const isActive = camb.classList.contains("active");
  if (shouldBeActive === isActive) return;

  if (shouldBeActive) {
    // 顯示前 force-sync 一次，避免上次 sync 後又漂掉
    const target = Math.max(0, main.currentTime + (state.camSyncOffsetB || 0));
    try {
      camb.currentTime = target;
    } catch (_) {
      // codec 還沒 ready 就忽略
    }
    camb.classList.add("active");
    if (!main.paused) camb.play().catch(() => {});
  } else {
    camb.classList.remove("active");
    camb.pause();
  }
  // overlay 切換後 crop UI 編輯目標也跟著切（implicit follow）
  // → 重畫 crop frame + ratio 按鈕到新的 active cam 對應狀態
  renderCropInfo();
  document.querySelectorAll(".ratio-btn").forEach((b) => {
    b.classList.toggle(
      "active",
      getActiveCropRatio() === b.dataset.ratio && getActiveCrop() != null,
    );
  });
}

function setupCameraMappingFollow() {
  $("#video").addEventListener("timeupdate", refreshCamBOverlay);
}
setupCameraMappingFollow();

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
      setUploadStatus(`上傳中 ${done} / ${files.length}：${res.name}（OK）`);
    } else {
      errors.push(`${res.name}：${res.error}`);
      setUploadStatus(
        `上傳中 ${done} / ${files.length}：${res.name}（失敗）`,
        true,
      );
    }
  }
  await loadFiles();
  renderFiles();
  if (errors.length === 0) {
    setUploadStatus(`已上傳 ${files.length} 個檔案到 01_母帶/`);
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

// 簡易 modal 控制（native <dialog>）
function showModal(id) {
  const el = $(`#${id}`);
  if (el && typeof el.showModal === "function" && !el.open) el.showModal();
}
function hideModal(id) {
  const el = $(`#${id}`);
  if (el && typeof el.close === "function" && el.open) el.close();
}

// 供應商 label + state key 對照表（避免散落 ternary）
function providerLabelOf(p) {
  return (
    {
      gemini: "Gemini",
      openai: "OpenAI whisper-1",
      whisper_mlx: "本地 Whisper（mlx）",
    }[p] || "Gemini"
  );
}
function hasKeyForProvider(p) {
  // 本地 provider 不需 key，視同永遠就緒
  if (p === "whisper_mlx") return true;
  if (p === "openai") return state.hasOpenAIKey;
  return state.hasGeminiKey;
}

// === 轉字幕流程 ===
function requestTranscribe(file) {
  // 預設一律走 mix 路徑（單一檔案 STT）。分軌轉錄串音問題明顯，改成進階手動開關。
  // 視情況顯示「改用分軌轉錄」or「設定並啟用分軌轉錄」按鈕在進階區塊。
  const hasMics = state.mics && Object.keys(state.mics).length > 0;
  const candidates = state.audioCandidates || [];
  const canSetupMics = !hasMics && candidates.length >= 2;
  const advanced = $("#transcribe-advanced");
  const perMicBtn = $("#transcribe-per-mic-btn");
  const micSetupBtn = $("#transcribe-mic-setup-btn");
  if (advanced) advanced.hidden = !(hasMics || canSetupMics);
  if (perMicBtn) {
    perMicBtn.hidden = !hasMics;
    perMicBtn.onclick = () => {
      hideModal("transcribe-modal");
      openPerMicTranscribe();
    };
  }
  if (micSetupBtn) {
    micSetupBtn.hidden = !canSetupMics;
    micSetupBtn.onclick = () => {
      hideModal("transcribe-modal");
      openMicSetup();
    };
  }
  const providerLabel = providerLabelOf(state.sttProvider);
  const hasSelectedKey = hasKeyForProvider(state.sttProvider);
  // 重置上次跑剩的進度條 + 兩顆按鈕（避免 success 殘留把 #transcribe-go 藏起來）
  $("#transcribe-progress").hidden = true;
  const go = $("#transcribe-go");
  const cancel = $("#transcribe-cancel");
  go.hidden = false;
  cancel.hidden = false;
  cancel.disabled = false;
  cancel.textContent = "取消";

  if (!hasSelectedKey) {
    // 警告色 + alert icon，跟其他錯誤 modal 視覺一致（純 textContent 太低調，
    // 之前使用者反映「沒看到提醒」）
    setModalStatusTitle(
      "transcribe-title",
      "circle-alert",
      "尚未設定 API key",
      "warning",
    );
    $("#transcribe-msg").innerHTML =
      `<div class="modal-error-text">請先到右上角「設定」設定 ${providerLabel} API key，才能轉字幕。</div>`;
    go.textContent = "去設定";
    go.disabled = false;
    go.onclick = () => {
      hideModal("transcribe-modal");
      openSettings();
    };
    cancel.onclick = () => hideModal("transcribe-modal");
    showModal("transcribe-modal");
    return;
  }

  setModalStatusTitle("transcribe-title", "mic", "轉字幕確認", "accent");
  $("#transcribe-msg").innerHTML =
    `來源檔：<code>${file.path}</code><br>` +
    `大小：${fmtSize(file.size)}<br><br>` +
    `用 ${providerLabel} STT 轉字幕並覆寫 <code>_v2.srt</code>。<br>` +
    `預估時間：約音檔長度的 1 倍（3 分鐘片約 60–180 秒）。`;
  go.textContent = "開始";
  go.disabled = false;
  go.onclick = () => runTranscribe(file);
  cancel.onclick = () => hideModal("transcribe-modal");
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
  $("#transcribe-advanced").hidden = true;
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

// 頁面載入時偵測背景中還在跑的 transcribe job：
// 自動把對應 modal 打開 + 啟 polling，避免使用者重整後以為沒在跑、
// 又從別的入口按一次而踩到 server 409「已有轉字幕正在進行中」。
async function resumeTranscribeIfRunning() {
  let s;
  try {
    const r = await fetch("/api/transcribe/status");
    if (!r.ok) return;
    s = await r.json();
  } catch (_) {
    return;
  }
  if (s.state !== "running") return;

  if (s.mode === "per-mic") {
    setModalStatusTitle("per-mic-title", null, "分軌轉錄中…", "");
    $("#per-mic-pick").hidden = true;
    $("#per-mic-progress").hidden = false;
    $("#per-mic-fill").style.width = "0%";
    $("#per-mic-percent").textContent = "0%";
    $("#per-mic-phase-label").textContent = "啟動中…";
    const speakers = Object.keys(s.mics_progress || {});
    if (speakers.length) renderPerMicProgressGrid(speakers);
    const go = $("#per-mic-go");
    const cancel = $("#per-mic-cancel");
    if (go) {
      go.hidden = true;
      go.disabled = true;
    }
    if (cancel) cancel.disabled = true;
    showModal("per-mic-modal");
    if (!_perMicPollTimer) {
      _perMicPollTimer = setInterval(pollPerMic, 500);
    }
    return;
  }

  // single mode（混音檔 STT）
  $("#transcribe-title").textContent = "轉字幕中…";
  $("#transcribe-msg").innerHTML =
    `處理中：<code>${s.src_path || ""}</code><br>` +
    `<em style="color:#888;font-size:12px">請保留這個分頁，不要關閉。</em>`;
  const adv = $("#transcribe-advanced");
  if (adv) adv.hidden = true;
  $("#transcribe-progress").hidden = false;
  $("#transcribe-fill").style.width = "0%";
  $("#transcribe-percent").textContent = "0%";
  $("#transcribe-phase-label").textContent = "啟動中…";
  renderTranscribePhasePills(s.phase || null, "running");
  const goBtn = $("#transcribe-go");
  const cancelBtn = $("#transcribe-cancel");
  if (goBtn) goBtn.disabled = true;
  if (cancelBtn) cancelBtn.disabled = true;
  showModal("transcribe-modal");
  if (!_transcribePollTimer) {
    _transcribePollTimer = setInterval(pollTranscribe, 500);
  }
}

// in-flight 防護：回應慢時 setInterval 會堆疊請求，亂序回來的舊回應會蓋掉新進度
let _pollTranscribeBusy = false;
async function pollTranscribe() {
  if (_pollTranscribeBusy) return;
  _pollTranscribeBusy = true;
  try {
    await _pollTranscribeOnce();
  } finally {
    _pollTranscribeBusy = false;
  }
}

async function _pollTranscribeOnce() {
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
  const go = $("#transcribe-go");
  if (ok) {
    $("#transcribe-fill").style.width = "100%";
    $("#transcribe-percent").textContent = "100%";
    $("#transcribe-phase-label").textContent = "完成";
    renderTranscribePhasePills(null, "done");
    setModalStatusTitle("transcribe-title", "circle-check", "完成", "success");
    $("#transcribe-msg").innerHTML =
      `已寫入：<code>${out_srt || "_v2.srt"}</code><br>編輯區已重新載入，可以繼續編輯字幕。`;

    await loadEpisodeState();
    renderTopbar();
    renderCards();
    renderCaption();
    renderTypo();
  } else {
    setModalStatusTitle("transcribe-title", "circle-alert", "失敗", "danger");
    $("#transcribe-msg").innerHTML =
      `<div class="modal-error-text">${error}</div>`;
    $("#transcribe-progress").hidden = true;
  }
  // success/error 都只留一顆主按鈕（成功 → 繼續編輯；失敗 → 關閉），避免出現
  // 一顆被禁用的「開始」+ 一顆「關閉」造成「下一步不明確」
  go.hidden = true;
  cancel.disabled = false;
  cancel.textContent = ok ? "繼續編輯" : "關閉";
  cancel.onclick = () => {
    hideModal("transcribe-modal");
    cancel.textContent = "取消";
    go.hidden = false;
    $("#transcribe-progress").hidden = true;
  };
}

// === 分軌設定 modal（yaml 沒設 mics 但有多軌音檔時跳這條） ===
// 流程：列出 audioCandidates → 三個 dropdown 對應 a/b/c → 預設用 Track*.wav 順序自動配
//   → 儲存 → POST /api/episode/mics → 重載 episode → 進分軌轉錄 modal
const MIC_SETUP_SPEAKERS = ["a", "b", "c"];

function guessMicAssignment(candidates) {
  // 嘗試從檔名抓 Track[1-3] / Mic[1-3] / Track 1 / Track-1 數字，依序配 a/b/c
  // 抓不到順序就照 candidates 原順序前 3 個配 a/b/c
  const numbered = [];
  for (const path of candidates) {
    const name = path.split("/").pop() || path;
    const m = name.match(/Track[\s_-]?(\d+)|Mic[\s_-]?(\d+)/i);
    if (m) {
      const n = parseInt(m[1] || m[2], 10);
      if (n >= 1 && n <= 3) numbered.push({ n, path });
    }
  }
  const result = { a: "", b: "", c: "" };
  if (numbered.length >= 2) {
    // 用 Track 編號配對
    numbered.sort((x, y) => x.n - y.n);
    const slots = ["a", "b", "c"];
    for (let i = 0; i < numbered.length && i < 3; i++) {
      result[slots[numbered[i].n - 1] || slots[i]] = numbered[i].path;
    }
  } else {
    // fallback：前 3 個檔依序給 a/b/c
    for (let i = 0; i < Math.min(3, candidates.length); i++) {
      result[MIC_SETUP_SPEAKERS[i]] = candidates[i];
    }
  }
  return result;
}

function renderMicSetupList(candidates, assignment) {
  const list = $("#mic-setup-list");
  list.innerHTML = "";
  for (const sp of MIC_SETUP_SPEAKERS) {
    const row = document.createElement("div");
    row.className = "mic-setup-row";
    row.dataset.speaker = sp;
    const options = ['<option value="">— 不設定 —</option>'];
    for (const path of candidates) {
      const selected = assignment[sp] === path ? " selected" : "";
      // path 可能含 " 等需要 escape
      const safe = path
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
      options.push(`<option value="${safe}"${selected}>${safe}</option>`);
    }
    row.innerHTML = `
      <span class="mic-setup-row-key">軌 ${sp}</span>
      <select class="mic-setup-row-select" data-speaker="${sp}">${options.join("")}</select>
    `;
    list.appendChild(row);
  }
  list.querySelectorAll(".mic-setup-row-select").forEach((sel) => {
    sel.addEventListener("change", updateMicSetupConflicts);
  });
  updateMicSetupConflicts();
}

function collectMicSetupAssignment() {
  const out = {};
  document.querySelectorAll(".mic-setup-row-select").forEach((sel) => {
    const sp = sel.dataset.speaker;
    const val = sel.value;
    if (val) out[sp] = val;
  });
  return out;
}

function updateMicSetupConflicts() {
  const assignment = collectMicSetupAssignment();
  const counts = {};
  for (const p of Object.values(assignment)) {
    counts[p] = (counts[p] || 0) + 1;
  }
  let hasConflict = false;
  document.querySelectorAll(".mic-setup-row").forEach((row) => {
    const sp = row.dataset.speaker;
    const val = assignment[sp];
    if (val && counts[val] > 1) {
      row.classList.add("conflict");
      hasConflict = true;
    } else {
      row.classList.remove("conflict");
    }
  });
  $("#mic-setup-warn").hidden = !hasConflict;
  // 至少要有一軌才能開始
  const anyPicked = Object.keys(assignment).length > 0;
  $("#mic-setup-go").disabled = hasConflict || !anyPicked;
}

function openMicSetup() {
  setModalStatusTitle("mic-setup-title", null, "設定分軌", null);
  const candidates = state.audioCandidates || [];
  $("#mic-setup-detected-count").textContent = String(candidates.length);
  const assignment = guessMicAssignment(candidates);
  renderMicSetupList(candidates, assignment);

  const go = $("#mic-setup-go");
  const cancel = $("#mic-setup-cancel");
  go.textContent = "儲存並開始";
  go.disabled = false;
  cancel.disabled = false;
  cancel.textContent = "取消";
  go.onclick = saveMicSetup;
  cancel.onclick = () => hideModal("mic-setup-modal");

  showModal("mic-setup-modal");
}

async function saveMicSetup() {
  const mics = collectMicSetupAssignment();
  if (!Object.keys(mics).length) {
    alert("至少要設定一軌");
    return;
  }
  const go = $("#mic-setup-go");
  const cancel = $("#mic-setup-cancel");
  go.disabled = true;
  cancel.disabled = true;
  go.textContent = "儲存中…";

  try {
    const r = await fetch("/api/episode/mics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mics }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
  } catch (e) {
    setModalStatusTitle(
      "mic-setup-title",
      "circle-alert",
      "儲存失敗",
      "danger",
    );
    $("#mic-setup-warn").textContent = `儲存失敗：${e.message}`;
    $("#mic-setup-warn").hidden = false;
    go.disabled = false;
    cancel.disabled = false;
    go.textContent = "重試";
    return;
  }

  // 重載 episode state → state.mics 才有值 → 接著開分軌轉錄 modal
  await loadEpisodeState();
  renderTopbar();
  renderCards();
  hideModal("mic-setup-modal");
  openPerMicTranscribe();
}

// === 分軌轉錄流程（episode.yaml.mics 有設時走這條） ===
// 流程：點轉字幕 → 開 modal 列 mics → 預設只勾「未轉過」的軌
//   → 按開始 → POST /api/transcribe/per-mic {speakers}
//   → 切到 progress 視圖 → 每軌 phase pill 即時更新（queued/vad/gemini/done/skipped/error）
//   → 全部 done 後跑 srt-merge → 完成 → 重載 episode 狀態
const PER_MIC_PHASE_LABELS = {
  queued: "等待中",
  vad: "VAD 切軌",
  gemini: "Gemini 轉錄",
  done: "完成",
  skipped: "已跳過",
  error: "失敗",
};
const PER_MIC_TOP_PHASE_LABELS = {
  "per-mic-transcribe": "分軌轉錄中",
  "srt-merge": "合併字幕",
};
let _perMicPollTimer = null;

function stopPerMicPoll() {
  if (_perMicPollTimer) {
    clearInterval(_perMicPollTimer);
    _perMicPollTimer = null;
  }
}

function renderPerMicList() {
  const list = $("#per-mic-list");
  const mics = state.mics || {};
  const existing = new Set(state.mic_srt_existing || []);
  const keys = Object.keys(mics).sort();
  if (!keys.length) {
    list.innerHTML = `<div class="modal-body-text">episode.yaml 沒有 mics 設定。</div>`;
    return;
  }
  list.innerHTML = "";
  for (const sp of keys) {
    const hasSrt = existing.has(sp);
    const path = mics[sp] || "";
    const row = document.createElement("label");
    row.className = "per-mic-row";
    row.innerHTML = `
      <input type="checkbox" class="per-mic-check" data-speaker="${sp}" ${hasSrt ? "" : "checked"}>
      <span class="per-mic-row-key">${sp}</span>
      <span class="per-mic-row-path">${path}</span>
      <span class="per-mic-row-status${hasSrt ? " existing" : ""}">${hasSrt ? "已轉過" : "未轉"}</span>
    `;
    list.appendChild(row);
  }
  list.querySelectorAll(".per-mic-check").forEach((cb) => {
    cb.addEventListener("change", updatePerMicOverwriteHint);
  });
  updatePerMicOverwriteHint();
}

function updatePerMicOverwriteHint() {
  const existing = new Set(state.mic_srt_existing || []);
  const checked = Array.from(
    document.querySelectorAll("#per-mic-list .per-mic-check:checked"),
  ).map((cb) => cb.dataset.speaker);
  const overwriting = checked.some((sp) => existing.has(sp));
  $("#per-mic-overwrite-hint").hidden = !overwriting;
}

function openPerMicTranscribe() {
  // reset 視圖
  setModalStatusTitle("per-mic-title", null, "分軌轉錄", null);
  $("#per-mic-pick").hidden = false;
  $("#per-mic-progress").hidden = true;
  $("#per-mic-progress-grid").innerHTML = "";
  $("#per-mic-fill").style.width = "0%";
  $("#per-mic-percent").textContent = "0%";
  $("#per-mic-phase-label").textContent = "啟動中…";

  renderPerMicList();

  const go = $("#per-mic-go");
  const cancel = $("#per-mic-cancel");
  go.hidden = false;
  go.disabled = false;
  go.textContent = "開始";
  cancel.disabled = false;
  cancel.textContent = "取消";
  go.onclick = runPerMicTranscribe;
  cancel.onclick = () => hideModal("per-mic-modal");

  $("#per-mic-select-all").onclick = () => {
    document
      .querySelectorAll("#per-mic-list .per-mic-check")
      .forEach((cb) => (cb.checked = true));
    updatePerMicOverwriteHint();
  };
  $("#per-mic-select-unconverted").onclick = () => {
    const existing = new Set(state.mic_srt_existing || []);
    document.querySelectorAll("#per-mic-list .per-mic-check").forEach((cb) => {
      cb.checked = !existing.has(cb.dataset.speaker);
    });
    updatePerMicOverwriteHint();
  };

  showModal("per-mic-modal");
}

function renderPerMicProgressGrid(speakers) {
  const grid = $("#per-mic-progress-grid");
  grid.innerHTML = "";
  for (const sp of speakers) {
    const row = document.createElement("div");
    row.className = "per-mic-progress-row";
    row.dataset.speaker = sp;
    row.innerHTML = `
      <span class="mic-tag">${sp}</span>
      <span class="mic-phase">等待中</span>
    `;
    grid.appendChild(row);
  }
}

function updatePerMicProgressGrid(micsProgress) {
  if (!micsProgress) return;
  for (const [sp, phase] of Object.entries(micsProgress)) {
    const row = document.querySelector(
      `#per-mic-progress-grid .per-mic-progress-row[data-speaker="${sp}"]`,
    );
    if (!row) continue;
    row.classList.remove("active", "done", "error");
    if (phase === "done" || phase === "skipped") {
      row.classList.add("done");
    } else if (phase === "error") {
      row.classList.add("error");
    } else if (phase === "vad" || phase === "gemini") {
      row.classList.add("active");
    }
    const ph = row.querySelector(".mic-phase");
    if (ph) ph.textContent = PER_MIC_PHASE_LABELS[phase] || phase;
  }
}

async function runPerMicTranscribe() {
  const speakers = Array.from(
    document.querySelectorAll("#per-mic-list .per-mic-check:checked"),
  ).map((cb) => cb.dataset.speaker);
  if (!speakers.length) {
    alert("至少要選一軌");
    return;
  }

  $("#per-mic-pick").hidden = true;
  $("#per-mic-progress").hidden = false;
  renderPerMicProgressGrid(speakers);
  $("#per-mic-fill").style.width = "0%";
  $("#per-mic-percent").textContent = "0%";
  $("#per-mic-phase-label").textContent = "啟動中…";

  const go = $("#per-mic-go");
  const cancel = $("#per-mic-cancel");
  go.disabled = true;
  cancel.disabled = true;

  try {
    const r = await fetch("/api/transcribe/per-mic", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speakers }),
    });
    if (!r.ok && r.status !== 202) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
  } catch (e) {
    finishPerMic({ ok: false, error: e.message });
    return;
  }

  _perMicPollTimer = setInterval(pollPerMic, 500);
}

// in-flight 防護：同 pollTranscribe
let _pollPerMicBusy = false;
async function pollPerMic() {
  if (_pollPerMicBusy) return;
  _pollPerMicBusy = true;
  try {
    await _pollPerMicOnce();
  } finally {
    _pollPerMicBusy = false;
  }
}

async function _pollPerMicOnce() {
  let s;
  try {
    const r = await fetch("/api/transcribe/status");
    s = await r.json();
  } catch (e) {
    return;
  }

  if (s.state === "idle") return;

  if (s.state === "running") {
    updatePerMicProgressGrid(s.mics_progress || {});
    const pct = Math.max(0, Math.min(100, s.percent || 0));
    const phase = s.phase || "per-mic-transcribe";
    // 整體進度條：分軌階段 0-90%，srt-merge 階段 90-100%
    let overall;
    if (phase === "srt-merge") {
      overall = 90 + pct * 0.1;
    } else {
      overall = pct * 0.9;
    }
    $("#per-mic-fill").style.width = `${overall.toFixed(1)}%`;
    $("#per-mic-percent").textContent = `${overall.toFixed(0)}%`;
    $("#per-mic-phase-label").textContent =
      PER_MIC_TOP_PHASE_LABELS[phase] || phase;
    return;
  }

  if (s.state === "done") {
    stopPerMicPoll();
    updatePerMicProgressGrid(s.mics_progress || {});
    finishPerMic({ ok: true, out_srt: s.out_srt });
    return;
  }

  if (s.state === "error") {
    stopPerMicPoll();
    updatePerMicProgressGrid(s.mics_progress || {});
    finishPerMic({ ok: false, error: s.error || "未知錯誤" });
    return;
  }
}

async function finishPerMic({ ok, out_srt, error }) {
  const cancel = $("#per-mic-cancel");
  const go = $("#per-mic-go");
  if (ok) {
    $("#per-mic-fill").style.width = "100%";
    $("#per-mic-percent").textContent = "100%";
    $("#per-mic-phase-label").textContent = "完成";
    setModalStatusTitle("per-mic-title", "circle-check", "完成", "success");
    $("#per-mic-progress-msg").innerHTML =
      `已寫入：<code>${out_srt || "_v2.srt"}</code><br>編輯區已重新載入。`;

    await loadEpisodeState();
    renderTopbar();
    renderCards();
    renderCaption();
    renderTypo();
  } else {
    setModalStatusTitle("per-mic-title", "circle-alert", "失敗", "danger");
    $("#per-mic-progress-msg").innerHTML =
      `<div class="modal-error-text">${error}</div>`;
  }
  go.hidden = true;
  cancel.disabled = false;
  cancel.textContent = ok ? "繼續編輯" : "關閉";
  cancel.onclick = () => {
    hideModal("per-mic-modal");
    cancel.textContent = "取消";
    go.hidden = false;
  };
}

// === 合成流程 ===
// 流程：點 🎬 合成 YT 或 📱 合成 Reels → 直接以該 target 啟動
//      → POST /api/assemble {targets, force} → modal 直接進入進度模式 + 開始 polling
//      → done/error 各自渲染收尾畫面
// 400「輸出已存在」會 confirm 後自動以 force=true 重打
let _assemblePollTimer = null;
// 記住上次合成的 targets / title / previewSec，給「重試」按鈕用
let _lastAssembleTargets = null;
let _lastAssembleTitle = null;
let _lastAssemblePreviewSec = null;

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

// 切換狀態 pill：starting / running / done / error
function setAssemblePill(stateName, label) {
  const pill = $("#assemble-pill");
  if (!pill) return;
  pill.setAttribute("data-state", stateName);
  $("#assemble-pill-label").textContent = label;
}

// 渲染輸出檔列表：每列檔名 + 「在 Finder 開啟」小按鈕
function renderAssembleOutputs(outs) {
  const wrap = $("#assemble-output");
  if (!outs || outs.length === 0) {
    wrap.hidden = true;
    wrap.innerHTML = "";
    return;
  }
  const revealPath = async (p) => {
    await fetch("/api/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: p }),
    });
  };
  wrap.innerHTML = "";
  outs.forEach((p) => {
    const row = document.createElement("div");
    row.className = "assemble-output-row";
    const name = document.createElement("span");
    name.className = "assemble-output-name";
    name.textContent = p;
    name.title = p;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "assemble-output-reveal";
    btn.textContent = "在 Finder 開啟";
    btn.onclick = async () => {
      try {
        await revealPath(p);
      } catch (e) {
        alert(`開啟失敗：${e.message}`);
      }
    };
    row.appendChild(name);
    row.appendChild(btn);
    wrap.appendChild(row);
  });
  wrap.hidden = false;
}

// 把 modal 重設成「進度模式」初始畫面：欄位歸零、按鈕回到預設
function resetAssembleModal() {
  $("#assemble-fill").style.width = "0%";
  $("#assemble-percent").textContent = "0%";
  $("#assemble-eta").textContent = "—";
  $("#assemble-current-label").textContent = "準備中…";
  $("#assemble-msg").textContent = "";
  setAssemblePill("starting", "啟動中");
  // 輸出列表、ffmpeg 訊息折疊：歸零並隱藏
  renderAssembleOutputs([]);
  const logWrap = $("#assemble-log-wrap");
  logWrap.hidden = true;
  logWrap.open = false;
  // 三顆按鈕：cancel 顯示為「取消」，retry / reveal 都隱藏
  const cancel = $("#assemble-cancel");
  cancel.disabled = false;
  cancel.textContent = "取消";
  const retry = $("#assemble-retry");
  retry.hidden = true;
  retry.onclick = null;
  const reveal = $("#assemble-reveal");
  reveal.hidden = true;
  reveal.onclick = null;
}

// 失敗收尾：顯示 retry 按鈕、把 cancel 改成「關閉」、把錯誤訊息塞進 ffmpeg log
function showAssembleErrorState(message) {
  setModalStatusTitle("assemble-title", "circle-alert", "合成失敗", "danger");
  setAssemblePill("error", "失敗");
  $("#assemble-current-label").textContent = "已停止";
  const logWrap = $("#assemble-log-wrap");
  $("#assemble-msg").textContent = message || "未知錯誤";
  logWrap.hidden = false;
  logWrap.open = true; // 失敗時預設展開讓使用者直接看到原因
  $("#assemble-cancel").textContent = "關閉";
  const retry = $("#assemble-retry");
  if (_lastAssembleTargets) {
    retry.hidden = false;
    retry.onclick = () => {
      resetAssembleModal();
      $("#assemble-title").textContent = _lastAssembleTitle || "合成中…";
      startAssemble(_lastAssembleTargets, {
        previewSec: _lastAssemblePreviewSec,
      });
    };
  }
}

// 由「合成 YT」/「合成 Reels」/「5 分鐘預覽」按鈕呼叫，targets 是單一字串陣列
// previewSec：若給正整數 → 截斷輸出長度 + 檔名加 .preview；預覽模式預設 force=true 方便反覆驗證
async function startAssemble(
  targets,
  { force = false, previewSec = null } = {},
) {
  _lastAssembleTargets = targets;
  _lastAssemblePreviewSec = previewSec;
  $("#assemble-title").textContent = "合成中…";
  setAssemblePill("running", "合成中");
  $("#assemble-current-label").textContent =
    "ffmpeg 啟動中（片頭 + 正片 + 片尾）";

  try {
    const body = { targets, force };
    if (previewSec) body.preview_sec = previewSec;
    const r = await fetch("/api/assemble", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const respBody = await r.json().catch(() => ({}));
      const msg = respBody.detail || `HTTP ${r.status}`;
      // 400「輸出已存在」→ 提供覆寫選項，使用者同意就以 force=true 重打
      if (r.status === 400 && /輸出已存在|--force/.test(msg) && !force) {
        if (confirm(`${msg}\n\n要覆寫並重新合成嗎？`)) {
          return startAssemble(targets, { force: true, previewSec });
        }
        hideModal("assemble-modal");
        return;
      }
      throw new Error(msg);
    }
  } catch (e) {
    setModalStatusTitle("assemble-title", "circle-alert", "無法啟動", "danger");
    setAssemblePill("error", "失敗");
    $("#assemble-current-label").textContent = "請求失敗";
    const logWrap = $("#assemble-log-wrap");
    $("#assemble-msg").textContent = e.message;
    logWrap.hidden = false;
    logWrap.open = true;
    $("#assemble-cancel").textContent = "關閉";
    return;
  }

  _assemblePollTimer = setInterval(pollAssemble, 1000);
}

// in-flight 防護：同 pollTranscribe
let _pollAssembleBusy = false;
async function pollAssemble() {
  if (_pollAssembleBusy) return;
  _pollAssembleBusy = true;
  try {
    await _pollAssembleOnce();
  } finally {
    _pollAssembleBusy = false;
  }
}

async function _pollAssembleOnce() {
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
      label = `[${(s.index || 0) + 1}/${s.total}] ${targetName} 合成中`;
    } else {
      label = `${targetName} 合成中`;
    }
    setAssemblePill("running", "合成中");
    $("#assemble-current-label").textContent = label;
    $("#assemble-percent").textContent = `${pct.toFixed(1)}%`;
    $("#assemble-eta").textContent = fmtEta(s.eta_s);
    $("#assemble-fill").style.width = `${pct.toFixed(1)}%`;
    return;
  }

  if (s.state === "done") {
    stopAssemblePoll();
    setModalStatusTitle(
      "assemble-title",
      "circle-check",
      "合成完成",
      "success",
    );
    setAssemblePill("done", "完成");
    $("#assemble-current-label").textContent = "輸出已寫入";
    $("#assemble-fill").style.width = "100%";
    $("#assemble-percent").textContent = "100%";
    $("#assemble-eta").textContent = "—";

    const outs = s.output_files || [];
    renderAssembleOutputs(outs);

    const reveal = $("#assemble-reveal");
    if (outs.length > 0) {
      reveal.hidden = false;
      reveal.onclick = async () => {
        try {
          await fetch("/api/reveal", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: outs[0] }),
          });
        } catch (e) {
          alert(`開啟失敗：${e.message}`);
        }
      };
      // 自動 reveal 第一個輸出；失敗就靜默退回手動按鈕
      fetch("/api/reveal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: outs[0] }),
      }).catch(() => {});
    }
    $("#assemble-cancel").textContent = "關閉";
    // 重新載入專案檔案列表，讓新合成檔出現在右側
    try {
      await loadFiles();
      renderFiles();
    } catch (_) {}
    return;
  }

  if (s.state === "error") {
    stopAssemblePoll();
    showAssembleErrorState(s.error || "未知錯誤");
    return;
  }
}

// 集中綁定 assemble 相關 listener，啟動時呼叫一次
function setupAssembleButtons() {
  const launch = (targets, title, { previewSec = null } = {}) => {
    if (hasUnsavedChanges()) {
      if (
        !confirm(
          "有未儲存的修改，建議先按「完成並儲存」再合成。\n仍要直接合成嗎？（會用磁碟上的 _v2.srt）",
        )
      ) {
        return;
      }
    }
    _lastAssembleTitle = title;
    resetAssembleModal();
    $("#assemble-title").textContent = title;
    showModal("assemble-modal");
    // 預覽模式預設 force=true，方便反覆驗證 bitrate / 畫質不被「輸出已存在」擋下
    startAssemble(targets, {
      previewSec,
      force: previewSec ? true : false,
    });
  };

  $("#assemble-yt-btn").addEventListener("click", () => {
    launch(["yt"], "合成 YT 16:9 完整版");
  });
  $("#assemble-reels-btn").addEventListener("click", () => {
    launch(["reels"], "合成 Reels 9:16 短版");
  });
  $("#assemble-preview-btn").addEventListener("click", () => {
    launch(["yt"], "合成 YT 前 5 分鐘預覽", { previewSec: 300 });
  });

  $("#assemble-cancel").addEventListener("click", () => {
    stopAssemblePoll();
    hideModal("assemble-modal");
  });
}

// === 設定 modal ===
const ASSET_PILL_LABEL = {
  intro: "intro",
  outro_audio: "outro 音樂",
  outro_image: "outro 卡片",
  logo: "浮水印 logo（選用）",
};

function renderAssetsPills() {
  const box = $("#settings-assets-pills");
  if (!box) return;
  box.innerHTML = "";
  const assets = state.assetsStatus || {};
  for (const key of ["intro", "outro_audio", "outro_image", "logo"]) {
    const info = assets[key];
    if (!info) continue;
    const pill = document.createElement("span");
    pill.className = `status-pill status-pill-${info.exists ? "ok" : "missing"}`;
    pill.title = info.path;
    pill.innerHTML = `<span class="status-dot" aria-hidden="true"></span><span class="status-label"></span><span class="status-mark">${info.exists ? "✓" : "✗"}</span>`;
    pill.querySelector(".status-label").textContent = ASSET_PILL_LABEL[key];
    box.appendChild(pill);
  }
}

function openSettings() {
  $("#settings-gemini-key").value = "";
  $("#settings-gemini-key").type = "password";
  $("#settings-openai-key").value = "";
  $("#settings-openai-key").type = "password";
  $("#settings-gemini-status").textContent = state.hasGeminiKey
    ? "已存在（重新輸入會覆蓋；留空則維持原樣）"
    : "尚未設定";
  $("#settings-openai-status").textContent = state.hasOpenAIKey
    ? "已存在（重新輸入會覆蓋；留空則維持原樣）"
    : "尚未設定";
  const provider = state.sttProvider || "gemini";
  const radio = document.querySelector(
    `input[name="settings-provider"][value="${provider}"]`,
  );
  if (radio) radio.checked = true;
  renderAssetsPills();
  showModal("settings-modal");
}

$("#settings-btn").addEventListener("click", openSettings);

$("#settings-cancel").addEventListener("click", () =>
  hideModal("settings-modal"),
);

$("#settings-show-gemini").addEventListener("click", () => {
  const input = $("#settings-gemini-key");
  input.type = input.type === "password" ? "text" : "password";
});

$("#settings-show-openai").addEventListener("click", () => {
  const input = $("#settings-openai-key");
  input.type = input.type === "password" ? "text" : "password";
});

$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const geminiKey = $("#settings-gemini-key").value.trim();
  const openaiKey = $("#settings-openai-key").value.trim();
  const provider =
    document.querySelector('input[name="settings-provider"]:checked')?.value ||
    "gemini";
  const payload = { provider };
  if (geminiKey) payload.gemini_api_key = geminiKey;
  if (openaiKey) payload.openai_api_key = openaiKey;
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
    state.hasGeminiKey = !!data.has_gemini_api_key;
    state.hasOpenAIKey = !!data.has_openai_api_key;
    state.sttProvider = ["openai", "whisper_mlx"].includes(data.provider)
      ? data.provider
      : "gemini";
    hideModal("settings-modal");
    renderFiles();
  } catch (e) {
    alert(`儲存失敗：${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "儲存";
  }
});

// === 詞庫 modal ===
// 工作集（modal 開啟時複製來自 server 的快照，避免使用者「取消」也已動到 state）
const glossaryWork = {
  episode: [], // [{canonical, sounds_like: [...], note}]
  common: [],
  yaml: [], // read-only
  activeTab: "episode",
};

async function openGlossary() {
  try {
    const r = await fetch("/api/glossary");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    glossaryWork.episode = (data.episode || []).map(cloneGlossaryEntry);
    glossaryWork.common = (data.common || []).map(cloneGlossaryEntry);
    glossaryWork.yaml = (data.yaml || []).map(cloneGlossaryEntry);
  } catch (e) {
    alert(`載入詞庫失敗：${e.message}`);
    return;
  }
  glossaryWork.activeTab = "episode";
  renderGlossary();
  showModal("glossary-modal");
}

function cloneGlossaryEntry(e) {
  return {
    canonical: String(e.canonical || ""),
    sounds_like: Array.isArray(e.sounds_like) ? e.sounds_like.map(String) : [],
    note: String(e.note || ""),
  };
}

function renderGlossary() {
  // tabs：active 樣式 + count
  document.querySelectorAll(".glossary-tab").forEach((btn) => {
    const active = btn.dataset.scope === glossaryWork.activeTab;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  $("#glossary-count-episode").textContent = String(
    glossaryWork.episode.length,
  );
  $("#glossary-count-common").textContent = String(glossaryWork.common.length);
  $("#glossary-count-yaml").textContent = String(glossaryWork.yaml.length);
  $("#glossary-pane-episode").hidden = glossaryWork.activeTab !== "episode";
  $("#glossary-pane-common").hidden = glossaryWork.activeTab !== "common";

  renderGlossaryList("episode");
  renderGlossaryList("common");
  renderGlossaryYamlList();
}

function renderGlossaryList(scope) {
  const list = $(`#glossary-list-${scope}`);
  const entries = glossaryWork[scope];
  list.innerHTML = "";
  if (entries.length === 0) {
    const empty = document.createElement("div");
    empty.className = "glossary-empty";
    empty.textContent =
      scope === "episode"
        ? "本集還沒有專屬詞庫條目。點「新增一條」開始。"
        : "全域詞庫是空的。加進來的條目所有集都會用到。";
    list.appendChild(empty);
    return;
  }
  entries.forEach((entry, idx) => {
    list.appendChild(buildGlossaryItem(scope, entry, idx));
  });
}

function renderGlossaryYamlList() {
  const list = $("#glossary-list-yaml");
  list.innerHTML = "";
  if (glossaryWork.yaml.length === 0) {
    const empty = document.createElement("div");
    empty.className = "glossary-empty";
    empty.textContent = "episode.yaml / defaults.yaml 沒有條目。";
    list.appendChild(empty);
    return;
  }
  glossaryWork.yaml.forEach((entry) => {
    const row = document.createElement("div");
    row.className = "glossary-item readonly";
    const left = document.createElement("div");
    left.className = "glossary-item-readonly-pill";
    left.textContent = entry.canonical;
    const mid = document.createElement("div");
    mid.className = "glossary-item-mid";
    const chips = document.createElement("div");
    chips.className = "glossary-chips";
    if (entry.sounds_like.length === 0) {
      const span = document.createElement("span");
      span.className = "modal-hint";
      span.textContent = "（無同音字）";
      chips.appendChild(span);
    } else {
      entry.sounds_like.forEach((s) => {
        const chip = document.createElement("span");
        chip.className = "glossary-chip";
        chip.textContent = s;
        chips.appendChild(chip);
      });
    }
    mid.appendChild(chips);
    if (entry.note) {
      const note = document.createElement("div");
      note.className = "modal-hint";
      note.textContent = entry.note;
      mid.appendChild(note);
    }
    row.appendChild(left);
    row.appendChild(mid);
    list.appendChild(row);
  });
}

function buildGlossaryItem(scope, entry, idx) {
  const row = document.createElement("div");
  row.className = "glossary-item";

  // 正式名
  const left = document.createElement("div");
  left.className = "glossary-item-canonical";
  const canInput = document.createElement("input");
  canInput.type = "text";
  canInput.placeholder = "正確寫法";
  canInput.value = entry.canonical;
  canInput.addEventListener("input", () => {
    entry.canonical = canInput.value;
  });
  left.appendChild(canInput);

  // 中間：sounds_like chips + note
  const mid = document.createElement("div");
  mid.className = "glossary-item-mid";

  const chips = document.createElement("div");
  chips.className = "glossary-chips";

  const renderChips = () => {
    chips.innerHTML = "";
    entry.sounds_like.forEach((sound, sIdx) => {
      const chip = document.createElement("span");
      chip.className = "glossary-chip";
      chip.textContent = sound;
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "×";
      del.title = "移除";
      del.addEventListener("click", () => {
        entry.sounds_like.splice(sIdx, 1);
        renderChips();
      });
      chip.appendChild(del);
      chips.appendChild(chip);
    });
    const input = document.createElement("input");
    input.type = "text";
    input.className = "glossary-chip-input";
    input.placeholder =
      entry.sounds_like.length === 0
        ? "Gemini 可能誤聽成（Enter / 逗號分隔）"
        : "+ 再加一個";
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === ",") {
        ev.preventDefault();
        const v = input.value.trim().replace(/,$/, "").trim();
        if (v && !entry.sounds_like.includes(v)) {
          entry.sounds_like.push(v);
          renderChips();
          const newInput = chips.querySelector(".glossary-chip-input");
          if (newInput) newInput.focus();
        } else {
          input.value = "";
        }
      } else if (ev.key === "Backspace" && input.value === "") {
        // 空 backspace → 移除最後一個 chip
        if (entry.sounds_like.length > 0) {
          entry.sounds_like.pop();
          renderChips();
          const newInput = chips.querySelector(".glossary-chip-input");
          if (newInput) newInput.focus();
        }
      }
    });
    input.addEventListener("blur", () => {
      const v = input.value.trim();
      if (v && !entry.sounds_like.includes(v)) {
        entry.sounds_like.push(v);
        renderChips();
      }
    });
    chips.appendChild(input);
  };
  renderChips();

  const noteWrap = document.createElement("div");
  noteWrap.className = "glossary-item-note";
  const noteInput = document.createElement("input");
  noteInput.type = "text";
  noteInput.placeholder = "備註（選填）";
  noteInput.value = entry.note;
  noteInput.addEventListener("input", () => {
    entry.note = noteInput.value;
  });
  noteWrap.appendChild(noteInput);

  mid.appendChild(chips);
  mid.appendChild(noteWrap);

  // 刪除整條
  const del = document.createElement("button");
  del.type = "button";
  del.className = "glossary-item-delete";
  del.title = "刪除這條";
  del.innerHTML = window.Icons.get("trash-2", { size: 14 });
  del.addEventListener("click", () => {
    glossaryWork[scope].splice(idx, 1);
    renderGlossary();
  });

  row.appendChild(left);
  row.appendChild(mid);
  row.appendChild(del);
  return row;
}

function addGlossaryEntry(scope) {
  glossaryWork[scope].push({ canonical: "", sounds_like: [], note: "" });
  renderGlossary();
  // 自動 focus 新一條的 canonical 輸入框
  const list = $(`#glossary-list-${scope}`);
  const lastInput = list.querySelectorAll(".glossary-item-canonical input")[
    glossaryWork[scope].length - 1
  ];
  if (lastInput) lastInput.focus();
}

async function saveGlossary() {
  const btn = $("#glossary-save");
  btn.disabled = true;
  const orig = btn.innerHTML;
  btn.textContent = "儲存中…";
  // 寫入前過濾：canonical 必填，trim 後為空的丟掉
  const clean = (arr) =>
    arr
      .map((e) => ({
        canonical: e.canonical.trim(),
        sounds_like: e.sounds_like.map((s) => s.trim()).filter(Boolean),
        note: e.note.trim(),
      }))
      .filter((e) => e.canonical);
  try {
    const [r1, r2] = await Promise.all([
      fetch("/api/glossary/episode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries: clean(glossaryWork.episode) }),
      }),
      fetch("/api/glossary/common", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries: clean(glossaryWork.common) }),
      }),
    ]);
    if (!r1.ok) throw new Error(`本集 HTTP ${r1.status}`);
    if (!r2.ok) throw new Error(`全域 HTTP ${r2.status}`);
    hideModal("glossary-modal");
  } catch (e) {
    alert(`儲存詞庫失敗：${e.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}

$("#glossary-btn").addEventListener("click", openGlossary);
$("#glossary-cancel").addEventListener("click", () =>
  hideModal("glossary-modal"),
);
$("#glossary-save").addEventListener("click", saveGlossary);
document.querySelectorAll(".glossary-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    glossaryWork.activeTab = btn.dataset.scope;
    renderGlossary();
  });
});
document.querySelectorAll(".glossary-add").forEach((btn) => {
  btn.addEventListener("click", () => addGlossaryEntry(btn.dataset.scope));
});

// === 鏡頭與音檔對齊 modal（4 個檔案全部手動下拉） ===
function openCamModal() {
  // cam A 下拉（從 01_母帶/*.mp4 挑）
  const camASel = $("#cam-a-select");
  camASel.innerHTML = "";
  const camAOpts = new Set(state.camACandidates || []);
  if (state.camAPath) camAOpts.add(state.camAPath);
  if (camAOpts.size === 0) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "（01_母帶/ 沒有 .mp4）";
    camASel.appendChild(o);
    camASel.disabled = true;
  } else {
    camASel.disabled = false;
    for (const path of [...camAOpts].sort()) {
      const o = document.createElement("option");
      o.value = path;
      o.textContent = path;
      if (path === state.camAPath) o.selected = true;
      camASel.appendChild(o);
    }
  }

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

  // 外接音檔下拉
  const audioSel = $("#audio-select");
  audioSel.innerHTML = "";
  const audioNone = document.createElement("option");
  audioNone.value = "";
  audioNone.textContent = "（無，用鏡頭原音）";
  audioSel.appendChild(audioNone);
  const currentAudio = state.audioPath || "";
  const audioOpts = new Set(state.audioCandidates || []);
  if (currentAudio) audioOpts.add(currentAudio);
  for (const path of [...audioOpts].sort()) {
    const o = document.createElement("option");
    o.value = path;
    o.textContent = path;
    if (path === currentAudio) o.selected = true;
    audioSel.appendChild(o);
  }
  $("#audio-sync-offset").value = state.audioSyncOffset
    ? String(state.audioSyncOffset)
    : "";

  // 字幕下拉（從 03_成品/ + 04_工作檔/ + 集根目錄挑 .srt）
  const srtSel = $("#srt-select");
  srtSel.innerHTML = "";
  const srtOpts = new Set(state.srtCandidates || []);
  if (state.srtPath) srtOpts.add(state.srtPath);
  if (srtOpts.size === 0) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "（尚未產生 _v2.srt）";
    srtSel.appendChild(o);
    srtSel.disabled = true;
  } else {
    srtSel.disabled = false;
    for (const path of [...srtOpts].sort()) {
      const o = document.createElement("option");
      o.value = path;
      o.textContent = path;
      if (path === state.srtPath) o.selected = true;
      srtSel.appendChild(o);
    }
  }

  showModal("cam-modal");
}

$("#cam-btn").addEventListener("click", openCamModal);
$("#cam-cancel").addEventListener("click", () => hideModal("cam-modal"));

// T23b: 自動對齊（音訊互相關）。前端只負責叫 endpoint + 把結果填回 input；
// 寫 yaml 仍走「儲存」按鈕，避免 race + 跟現有設計一致。
$("#cam-auto-align").addEventListener("click", async () => {
  const camAPath = $("#cam-a-select").value || "";
  const camBPath = $("#cam-b-select").value || "";
  if (!camBPath) {
    alert("請先選 cam B 來源");
    return;
  }
  const btn = $("#cam-auto-align");
  btn.disabled = true;
  const stopSpin = startBtnSpinner(btn, "計算中");
  try {
    const r = await fetch("/api/auto-align", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cam_a_path: camAPath, cam_b_path: camBPath }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    $("#cam-sync-offset-b").value = data.offset_sec.toFixed(3);
  } catch (e) {
    alert(`自動對齊失敗：${e.message}`);
  } finally {
    stopSpin();
    btn.disabled = false;
    setBtnLabel(btn, "target", "自動對齊");
  }
});

// 抓 cam-modal 目前狀態組 /api/save payload；align-all auto-save 跟 cam-save 共用，避免 drift
function _buildCamModalSavePayload() {
  const camAPath = $("#cam-a-select").value || "";
  const camBPath = $("#cam-b-select").value || "";
  const audioPath = $("#audio-select").value || "";
  const srtPath = $("#srt-select").value || "";
  const offset = Number($("#cam-sync-offset-b").value || 0);
  const audioOffset = Number($("#audio-sync-offset").value || 0);
  return {
    crop_yt: serializeCropForSave(state.cropYt, state.cropYtB),
    crop_reels: serializeCropForSave(state.cropReels, state.cropReelsB),
    deletions: [...state.deletions],
    head_trim_sec: state.headTrimSec,
    tail_trim_sec: state.tailTrimSec,
    cards: [...state.textOverrides.entries()].map(([idx, text]) => ({
      idx,
      text,
    })),
    cameras_mapping: Object.fromEntries(state.camerasMapping),
    speakers_mapping: Object.fromEntries(state.speakersMapping),
    splits: Object.fromEntries(state.cardSplits),
    cam_a_path: camAPath,
    cam_b_path: camBPath,
    camera_sync_offset_b: Number.isFinite(offset) ? offset : 0,
    audio: {
      path: audioPath,
      sync_offset: Number.isFinite(audioOffset) ? audioOffset : 0,
    },
    srt_path: srtPath,
  };
}

// 一鍵全部對齊：並行打兩次 /api/auto-align（cam B + 音檔），各自填回對應 input。
// 只選一邊就只跑那邊；都沒選 → 提示。完成後自動 /api/save，預覽立即跟上。
$("#align-all").addEventListener("click", async () => {
  const camAPath = $("#cam-a-select").value || "";
  const camBPath = $("#cam-b-select").value || "";
  const audioPath = $("#audio-select").value || "";
  if (!camBPath && !audioPath) {
    alert("請先選 cam B 或音檔（兩邊都沒選等於沒事可做）");
    return;
  }
  const btn = $("#align-all");
  btn.disabled = true;
  let stopSpin = startBtnSpinner(btn, "計算中");

  try {
    const tasks = [];
    if (camBPath) {
      tasks.push(
        fetch("/api/auto-align", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cam_a_path: camAPath, cam_b_path: camBPath }),
        }).then(async (r) => {
          if (!r.ok) {
            const err = await r
              .json()
              .catch(() => ({ detail: `HTTP ${r.status}` }));
            throw new Error(`cam B：${err.detail || r.status}`);
          }
          const data = await r.json();
          $("#cam-sync-offset-b").value = data.offset_sec.toFixed(3);
        }),
      );
    }
    if (audioPath) {
      tasks.push(
        fetch("/api/auto-align", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cam_a_path: camAPath, audio_path: audioPath }),
        }).then(async (r) => {
          if (!r.ok) {
            const err = await r
              .json()
              .catch(() => ({ detail: `HTTP ${r.status}` }));
            throw new Error(`音檔：${err.detail || r.status}`);
          }
          const data = await r.json();
          $("#audio-sync-offset").value = data.offset_sec.toFixed(3);
        }),
      );
    }

    const results = await Promise.allSettled(tasks);
    const errors = results
      .filter((r) => r.status === "rejected")
      .map((r) => r.reason.message);
    if (errors.length) {
      // 有錯就不要自動 save，避免把錯誤值寫進 yaml
      stopSpin();
      setBtnLabel(btn, "target", "一鍵全部對齊（cam B + 音檔）並儲存");
      alert(`部分對齊失敗：\n${errors.join("\n")}`);
      return;
    }

    // 全部成功 → 自動 save + 重抓 state + 重綁外接音檔
    stopSpin();
    stopSpin = startBtnSpinner(btn, "儲存中");
    const payload = _buildCamModalSavePayload();
    await postSave(payload);
    await loadEpisodeState();
    renderTopbar();
    renderCards();
    setupExternalAudio();
    // cam A 可能被換掉；/api/video URL 不變，靠 cache-buster 強制 reload 主預覽
    const video = $("#video");
    video.src = `/api/video?_=${Date.now()}`;
    video.load();
    stopSpin();
    setBtnLabel(btn, "circle-check", "已對齊並儲存");
    setTimeout(() => {
      setBtnLabel(btn, "target", "一鍵全部對齊（cam B + 音檔）並儲存");
    }, 2000);
  } catch (e) {
    alert(`對齊或儲存失敗：${e.message}`);
    stopSpin();
    setBtnLabel(btn, "target", "一鍵全部對齊（cam B + 音檔）並儲存");
  } finally {
    btn.disabled = false;
  }
});

// 外接音檔自動對齊（cam A vs audio file 互相關），跟 cam B 走同一條 /api/auto-align
$("#audio-auto-align").addEventListener("click", async () => {
  const camAPath = $("#cam-a-select").value || "";
  const audioPath = $("#audio-select").value || "";
  if (!audioPath) {
    alert("請先選音檔來源");
    return;
  }
  const btn = $("#audio-auto-align");
  btn.disabled = true;
  const stopSpin = startBtnSpinner(btn, "計算中");
  try {
    const r = await fetch("/api/auto-align", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cam_a_path: camAPath, audio_path: audioPath }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
      throw new Error(err.detail || `HTTP ${r.status}`);
    }
    const data = await r.json();
    $("#audio-sync-offset").value = data.offset_sec.toFixed(3);
  } catch (e) {
    alert(`自動對齊失敗：${e.message}`);
  } finally {
    stopSpin();
    btn.disabled = false;
    setBtnLabel(btn, "target", "自動對齊");
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
  showModal("manual-align-modal");
});

$("#manual-align-cancel").addEventListener("click", () => {
  hideModal("manual-align-modal");
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
  hideModal("manual-align-modal");
});

$("#cam-save").addEventListener("click", async () => {
  const camAPath = $("#cam-a-select").value || "";
  const camBPath = $("#cam-b-select").value || "";
  const offsetRaw = $("#cam-sync-offset-b").value;
  const offset = offsetRaw === "" ? 0 : Number(offsetRaw);
  if (!Number.isFinite(offset)) {
    alert("同步偏移要是數字");
    return;
  }
  const audioPath = $("#audio-select").value || "";
  const audioOffsetRaw = $("#audio-sync-offset").value;
  const audioOffset = audioOffsetRaw === "" ? 0 : Number(audioOffsetRaw);
  if (!Number.isFinite(audioOffset)) {
    alert("音檔同步偏移要是數字");
    return;
  }
  const btn = $("#cam-save");
  btn.disabled = true;
  btn.textContent = "儲存中…";
  // 只送 cam A/B 相關欄位 + 必填的 deletions/cards（保留現有編輯）
  const payload = {
    crop_yt: serializeCropForSave(state.cropYt, state.cropYtB),
    crop_reels: serializeCropForSave(state.cropReels, state.cropReelsB),
    deletions: [...state.deletions],
    head_trim_sec: state.headTrimSec,
    tail_trim_sec: state.tailTrimSec,
    cards: [...state.textOverrides.entries()].map(([idx, text]) => ({
      idx,
      text,
    })),
    cameras_mapping: Object.fromEntries(state.camerasMapping),
    speakers_mapping: Object.fromEntries(state.speakersMapping),
    splits: Object.fromEntries(state.cardSplits),
    cam_a_path: camAPath,
    cam_b_path: camBPath,
    camera_sync_offset_b: offset,
    audio: { path: audioPath, sync_offset: audioOffset },
  };
  try {
    await postSave(payload);
    // 重抓 episode state 讓 A/B toggle 即刻反映新 cameras
    await loadEpisodeState();
    renderTopbar();
    renderCards();
    setupExternalAudio();
    // cam A 可能被換掉；/api/video URL 不變，靠 cache-buster 強制 reload 主預覽
    const video = $("#video");
    video.src = `/api/video?_=${Date.now()}`;
    video.load();
    hideModal("cam-modal");
  } catch (e) {
    alert(`儲存失敗：${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "儲存";
  }
});

// 離開頁面（重整 / 關分頁 / 上一頁）攔截；瀏覽器只允許 generic 提示文字
window.addEventListener("beforeunload", (e) => {
  if (!hasUnsavedChanges()) return;
  e.preventDefault();
  e.returnValue = "";
});

$("#cancel-btn").addEventListener("click", async () => {
  if (hasUnsavedChanges() && !confirm("未儲存的修改會丟失，確定取消？")) return;
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
  err.classList.remove("is-success");
  err.hidden = false;
}

function showSwitchSuccess(msg) {
  const err = $("#ep-switch-error");
  err.textContent = msg;
  err.classList.add("is-success");
  err.hidden = false;
  clearTimeout(showSwitchSuccess._t);
  showSwitchSuccess._t = setTimeout(() => {
    err.hidden = true;
    err.textContent = "";
    err.classList.remove("is-success");
  }, 3000);
}

function clearSwitchError() {
  const err = $("#ep-switch-error");
  err.textContent = "";
  err.classList.remove("is-success");
  err.hidden = true;
}

async function pickEpisodeFolder() {
  const btn = $("#ep-switch-btn");
  if (hasUnsavedChanges() && !confirm("有未儲存的修改，換集後會丟失，繼續？"))
    return;

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
      _setInitRow(
        row,
        e.is_dir ? "folder" : "file",
        `${e.name}${e.is_dir ? "/" : ""}`,
      );
      cur.appendChild(row);
    }
  }
  const create = $("#init-create-list");
  create.innerHTML = "";
  for (const d of preview.subdirs_to_create) {
    const row = document.createElement("div");
    row.className = "row dir new";
    _setInitRow(row, "folder", `${d}/`);
    create.appendChild(row);
  }
  const yamlRow = document.createElement("div");
  yamlRow.className = "row new";
  _setInitRow(yamlRow, "file", "episode.yaml");
  create.appendChild(yamlRow);
  const todoRow = document.createElement("div");
  todoRow.className = "row new";
  _setInitRow(todoRow, "file", "TODO.md");
  create.appendChild(todoRow);

  const modal = $("#init-modal");
  modal.dataset.path = preview.path;
  showModal("init-modal");
}

function closeInitModal() {
  const modal = $("#init-modal");
  hideModal("init-modal");
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
    // 換集後強制把 drawer 展開並切到 files tab，避免 stale localStorage
    // 讓使用者誤以為新集沒有檔案（實際只是 drawer collapsed 或停在 typo tab）
    try {
      const drawer = $("#drawer");
      if (drawer) {
        drawer.classList.remove("collapsed");
        localStorage.setItem("edit.drawer.collapsed", "0");
        const tabs = drawer.querySelectorAll(".drawer-tab");
        const panes = drawer.querySelectorAll(".drawer-pane");
        tabs.forEach((t) => {
          const active = t.dataset.drawerTab === "files";
          t.classList.toggle("active", active);
          t.setAttribute("aria-selected", active ? "true" : "false");
          t.setAttribute("tabindex", active ? "0" : "-1");
        });
        panes.forEach((p) => {
          p.hidden = p.dataset.drawerPane !== "files";
        });
        localStorage.setItem("edit.drawer.tab", "files");
      }
    } catch (_) {}
    showSwitchSuccess(`✓ 已切換到「${state.name || newPath}」`);
  } catch (e) {
    showSwitchError(`換集失敗：${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = origLabel;
  }
}

$("#ep-switch-btn").addEventListener("click", pickEpisodeFolder);
document
  .getElementById("back-to-dash-btn")
  ?.addEventListener("click", async () => {
    const r = await fetch("/api/episodes/close", { method: "POST" });
    if (!r.ok) {
      alert("回 dashboard 失敗");
      return;
    }
    window.location.href = "/";
  });
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
  if (hasUnsavedChanges() && !confirm("有未儲存的修改，新建集後會丟失，繼續？"))
    return;
  $("#new-ep-date").value = todayYmd();
  $("#new-ep-name").value = "";
  $("#new-ep-error").hidden = true;
  $("#new-ep-error").textContent = "";
  updateNewEpPreview();
  showModal("new-ep-modal");
  $("#new-ep-name").focus();
}

function closeNewEpModal() {
  hideModal("new-ep-modal");
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
setupReelsClips();
setupAssembleButtons();
setupSusToolbar();

// 注入靜態 [data-icon] span（topbar、modal head、accordion summary 等）
if (window.Icons) window.Icons.inject();

// 影片框尺寸變動就重算字幕 px（sidebar 收合、視窗縮放、瀏覽器 zoom 都會觸發）
// 用 ResizeObserver 抓 .video-wrap 而不是 window.resize，因為 sidebar 收合不會觸發 resize
(() => {
  const wrap = document.querySelector(".video-wrap");
  if (!wrap || typeof ResizeObserver === "undefined") return;
  const ro = new ResizeObserver(() => applyCaptionFontSize());
  ro.observe(wrap);
})();
