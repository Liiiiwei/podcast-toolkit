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
  // 旋轉拉正：per cam 度數（綁源攝影機，YT/Reels 共用）；正值順時針，搭配 crop 把黑角裁掉
  rotate: { a: 0, b: 0 },
  deletions: new Set(),
  susChecked: new Set(), // 紅卡批次刪除的 checkbox 勾選集合（card.idx）
  reviewFilter: false, // 「只看待複查卡」篩選開關（needs_review / suspicious_pause）
  reviewSeen: new Set(), // 已人工複查過的待複查卡（card.idx）；session 內、不寫檔、不進 undo、換集即清
  cards: [],
  textOverrides: new Map(), // idx -> text
  // 在 UI 上按 Enter 切句：oldIdx -> [part0_text, part1_text, ...]；存檔時翻譯成
  // SRT 新編號，並把 deletions / camerasMapping 從 "5" 散成 "5:0" / "5:1"。
  // 切過的卡 textOverrides 會被清掉（parts 內容才是真相）。
  // sub-card 上按 Enter 可連鎖切：把該 part 拆兩半、後面 composite key 全部 +1。
  cardSplits: new Map(),
  // 在字卡最前面按 Backspace 跨卡合併：被併掉的整卡 idx 集合。被併卡不單獨顯示 /
  // 不寫進 SRT，只把結束時間接到上一張整卡；合併後文字落在上一張的 textOverrides。
  // 只支援「整卡併整卡」（切過的卡在卡內已有 sub-card 合併，不走這條）。
  cardMerges: new Set(),
  // 單卡時間微調：idx -> {start, end}（覆寫該卡時間）；只用於未切的整卡
  timeOverrides: new Map(),
  timeEditKey: null, // 目前展開時間微調工具列的卡 domKey（字串；null = 沒開）
  // 新增的字卡：[{tempId, start, end, text}]；存檔時 append 進 SRT、重編號
  newCards: [],
  newCardSeq: 0, // tempId 流水號
  dragCardIdx: null, // 拖拉換位置中的整卡 idx（null = 沒在拖）
  // 時間軸拖拉改的字幕時間：composite key（int 或 "idx:part"）→ {start, end} 秒。
  // 疊在 expandedCards 衍生時間最外層；存檔寫進 _v2.srt。切句會清掉該卡的覆寫。
  cardTimings: new Map(),
  tlZoom: 1, // 字幕時間軸縮放倍率（1 = 適合畫面寬；>1 = 放大攤開 + 橫向捲動）
  waveform: null, // 時間軸波形資料 {peaks, silences, duration,...}；後端 /api/waveform 算好，背景載入
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
  // 鏡頭規則（分軌設定 modal 用）：{home, feature:{speaker:cam}, min_sec}；feature 有的 speaker = 來賓
  cameraRule: {},
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
  // 節目封面（右上角小徽章）開關 / 正片倍速 {enabled,factor} / 合成字幕模式
  coverEnabled: false,
  speed: { enabled: true, factor: 1.15 },
  // 全片去空拍（偵測中段靜音→跳剪）：{enabled, minSilence 秒}。在合成設定視窗設、存進 episode.yaml
  silenceTrim: { enabled: true, minSilence: 0.8 },
  subtitleMode: "burn", // "burn"=燒進畫面 | "sidecar"=另存字幕檔（影片不燒）
  // 非破壞性字幕偏移（秒）：存 episode.yaml，預覽 + 合成都套，原 _v2.srt 不動。正值=字幕往後延。
  subtitleOffsetSec: 0,
  // 旋轉 / 封面 / 倍速這類「輸出設定」有沒有動過（unsavedCount 用；存檔/載入後歸零）
  outputDirty: false,
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
    rotate: { a: state.rotate.a, b: state.rotate.b },
    camerasMapping: new Map(state.camerasMapping),
    speakersMapping: new Map(state.speakersMapping),
    headTrimSec: state.headTrimSec,
    tailTrimSec: state.tailTrimSec,
    reelsClips: state.reelsClips.map((c) => ({ ...c })),
    cardSplits: new Map([...state.cardSplits].map(([k, v]) => [k, v.slice()])),
    cardMerges: new Set(state.cardMerges),
    timeOverrides: new Map(
      [...state.timeOverrides].map(([k, v]) => [k, { ...v }]),
    ),
    newCards: state.newCards.map((c) => ({ ...c })),
    cardTimings: new Map([...state.cardTimings].map(([k, v]) => [k, { ...v }])),
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
  state.rotate = snap.rotate ? { ...snap.rotate } : { a: 0, b: 0 };
  state.camerasMapping = new Map(snap.camerasMapping);
  state.speakersMapping = new Map(snap.speakersMapping || []);
  state.headTrimSec = snap.headTrimSec;
  state.tailTrimSec = snap.tailTrimSec;
  state.reelsClips = (snap.reelsClips || []).map((c) => ({ ...c }));
  state.cardSplits = new Map(
    [...(snap.cardSplits || [])].map(([k, v]) => [k, v.slice()]),
  );
  state.cardMerges = new Set(snap.cardMerges || []);
  state.timeOverrides = new Map(
    [...(snap.timeOverrides || [])].map(([k, v]) => [k, { ...v }]),
  );
  state.newCards = (snap.newCards || []).map((c) => ({ ...c }));
  state.cardTimings = new Map(
    [...(snap.cardTimings || [])].map(([k, v]) => [k, { ...v }]),
  );
}

function pushUndo() {
  state.undoStack.push(snapshotEditState());
  if (state.undoStack.length > UNDO_MAX) state.undoStack.shift();
  state.redoStack = [];
}

// 切句 / 合併會重算該卡各段時間（allocate_split_times），舊的手動時間覆寫失效 →
// 清掉這張原卡的 int key 與所有 "idx:part" composite key。
function clearCardTimings(idx) {
  state.cardTimings.delete(idx);
  const prefix = `${idx}:`;
  for (const k of [...state.cardTimings.keys()]) {
    if (typeof k === "string" && k.startsWith(prefix)) {
      state.cardTimings.delete(k);
    }
  }
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

// 切卡/合併這類操作要同步搬 deletions / camerasMapping / textOverrides /
// cardSplits 多個結構的 key，中途丟例外會留下孤兒 key（字卡錯位，undo 也救不回，
// 因為半套狀態已經蓋掉工作區）。包成 transaction：先 pushUndo 再跑 fn；
// 失敗就還原快照、撤掉剛 push 的 undo 紀錄、整體重繪。
function mutateEditStateAtomic(fn) {
  pushUndo();
  const snap = snapshotEditState();
  try {
    fn();
  } catch (err) {
    applyEditSnapshot(snap);
    state.undoStack.pop(); // 這筆 transaction 沒成立，撤掉剛 push 的紀錄
    rerenderEditState();
    console.error("編輯操作失敗，狀態已還原：", err);
    throw err;
  }
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

// 編輯工作流快捷鍵（無修飾鍵）：Space 播放/暫停、↑/↓ 切前後字幕卡、? 開快捷鍵總覽。
// 字幕編輯框 / input 聚焦時讓出原生行為（打字、IME、捲動）；有 modal 開著時也不攔。
document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  if (
    t &&
    (t.tagName === "INPUT" ||
      t.tagName === "TEXTAREA" ||
      t.isContentEditable === true)
  ) {
    return;
  }
  if (document.querySelector("dialog[open]")) return;
  const key = e.key;
  if (key === " " || key === "Spacebar") {
    e.preventDefault();
    togglePlay();
  } else if (key === "ArrowDown") {
    e.preventDefault();
    stepCard(1);
  } else if (key === "ArrowUp") {
    e.preventDefault();
    stepCard(-1);
  } else if (key === "p" || key === "P") {
    // 試聽當前播放位置所在的字幕卡（從卡頭播到卡尾自動停）
    e.preventDefault();
    const r = activeCardAt($("#video").currentTime);
    if (r) auditionCard(r);
  } else if (key === "j" || key === "J") {
    // 跳下一張待複查卡（沒有待複查卡時自動 no-op）
    e.preventDefault();
    jumpToNextReview();
  } else if (key === "k" || key === "K") {
    // 跳上一張待複查卡
    e.preventDefault();
    jumpToPrevReview();
  } else if (key === "?") {
    e.preventDefault();
    showModal("shortcuts-modal");
  }
});

const $ = (sel) => document.querySelector(sel);

// 秒 → "m:ss.d"（trim 拖把 tooltip 用，0.1s 精度跟 trim 值一致）
function fmtTimeD(sec) {
  if (!isFinite(sec)) return "0:00.0";
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  return `${m}:${s < 10 ? "0" : ""}${s.toFixed(1)}`;
}

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
  // 單一真相來源：與 unsavedCount() 對齊。先前漏算 timeOverrides/newCards/outputDirty，
  // 導致「只做單卡時間微調或新增字卡」後，換集/關頁前的未存保護不觸發 → 默默丟失那些編輯。
  return unsavedCount() > 0;
}

function unsavedCount() {
  return (
    state.deletions.size +
    state.textOverrides.size +
    state.cardSplits.size +
    state.cardMerges.size +
    state.timeOverrides.size +
    state.newCards.length +
    state.cardTimings.size +
    (state.cropYt != null ? 1 : 0) +
    (state.cropReels != null ? 1 : 0) +
    (state.outputDirty ? 1 : 0)
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
  const merged = state.cardMerges.size;
  const head = state.headTrimSec || 0;
  const tail = state.tailTrimSec || 0;
  let line = `字幕卡 ${total} 段 · 已刪 ${deleted} · 已修 ${dirty}`;
  if (split > 0) line += ` · 已切 ${split}`;
  if (merged > 0) line += ` · 已併 ${merged}`;
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

// === 字幕字級調整（crop 比例旁）：依目前分頁調 YT / Reels 各自的 subtitle_style.font_size ===
// 直接改 state 裡的 style 物件 + 即時預覽；存檔時 buildSavePayload 帶上、save_state 寫進 yaml。
function activeSubtitleStyle() {
  return state.activeVersion === "reels"
    ? state.subtitleStyleReels
    : state.subtitleStyleYt;
}
function renderCaptionSizeControl() {
  const valEl = $("#cap-size-val");
  if (!valEl) return;
  const style = activeSubtitleStyle();
  const fs = style && Number(style.font_size);
  valEl.textContent = fs ? String(Math.round(fs)) : "—";
  const dec = $("#cap-size-dec");
  const inc = $("#cap-size-inc");
  if (dec) dec.disabled = !fs;
  if (inc) inc.disabled = !fs;
}
function nudgeCaptionSize(delta) {
  const style = activeSubtitleStyle();
  const cur = style && Number(style.font_size);
  if (!cur) return;
  const next = Math.max(12, Math.min(200, Math.round(cur + delta)));
  if (next === cur) return;
  style.font_size = next;
  state.outputDirty = true; // 進「未儲存」計數，按「完成並儲存」才落地
  applyCaptionFontSize();
  renderCaptionSizeControl();
  renderTopbar();
}
function setupCaptionSize() {
  $("#cap-size-dec")?.addEventListener("click", () => nudgeCaptionSize(-2));
  $("#cap-size-inc")?.addEventListener("click", () => nudgeCaptionSize(2));
}

// 旋轉預覽：對 cam A (#video) / cam B (#video-camb) 各自套 CSS rotate，對齊 ffmpeg
// 「先 rotate 源、再從軸對齊矩形 crop」語意。crop-frame / caption-overlay 不旋轉（維持軸對齊）。
function applyRotationPreview() {
  const a = Number(state.rotate?.a) || 0;
  const b = Number(state.rotate?.b) || 0;
  const v = document.querySelector("#video");
  const vb = document.querySelector("#video-camb");
  if (v) v.style.transform = a ? `rotate(${a}deg)` : "";
  if (vb) vb.style.transform = b ? `rotate(${b}deg)` : "";
}

// 旋轉控制目前編哪台（跟 crop 共用 A/B context）；單機集恆 "a"
function activeRotateCam() {
  return getActiveCropCam();
}

// 同步旋轉滑桿 / 數字 / 徽章到目前 active cam 的角度
function syncRotateControls() {
  const cam = activeRotateCam();
  const deg = Number(state.rotate?.[cam]) || 0;
  const slider = document.querySelector("#rotate-slider");
  const num = document.querySelector("#rotate-input");
  const badge = document.querySelector("#rotate-cam-badge");
  if (slider) slider.value = String(deg);
  if (num) num.value = String(deg);
  if (badge) {
    const hasCamB = !!(state.cameras && state.cameras.b);
    badge.textContent = hasCamB ? (cam === "b" ? "B" : "A") : "";
  }
}

function renderCropInfo() {
  applyRotationPreview();
  syncRotateControls();
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
  let lastWhole = null; // 最後輸出的整卡；被 Backspace 併掉的卡把結束時間接到它
  for (const c of state.cards) {
    // 跨卡合併掉的整卡：不輸出，只把結束時間延伸到本卡結束（時間 = 上一張.start → 本卡.end）
    if (state.cardMerges.has(c.idx)) {
      if (lastWhole) {
        const mov = state.timeOverrides.get(c.idx);
        lastWhole.end = mov ? mov.end : c.end;
      }
      continue;
    }
    // 時間微調 override：未切卡直接套；切過的卡用 override 當 t0/t1 重算 sub-card
    const ov = state.timeOverrides.get(c.idx);
    const cStart = ov ? ov.start : c.start;
    const cEnd = ov ? ov.end : c.end;
    const parts = state.cardSplits.get(c.idx);
    if (parts && parts.length > 1) {
      const lengths = parts.map((p) => Math.max((p || "").length, 1));
      const total = lengths.reduce((a, b) => a + b, 0);
      const t0 = cStart;
      const t1 = cEnd;
      const dur = t1 - t0;
      const budget = total * SPLIT_SEC_PER_CHAR;
      const rate = budget <= dur ? SPLIT_SEC_PER_CHAR : dur / total;
      let cum = 0;
      for (let i = 0; i < parts.length; i++) {
        const start = t0 + cum;
        cum += lengths[i] * rate;
        const end = Math.min(t0 + cum, t1);
        const key = `${c.idx}:${i}`;
        const ov = state.cardTimings.get(key);
        out.push({
          c,
          partIdx: i,
          key,
          text: parts[i],
          start: ov ? ov.start : start,
          end: ov ? ov.end : end,
        });
      }
      lastWhole = null; // 切過的卡不當合併目標（後端無法把合併文字掛到切卡上）
    } else {
      const ov = state.cardTimings.get(c.idx);
      out.push({
        c,
        partIdx: null,
        key: c.idx,
        text: state.textOverrides.get(c.idx) ?? c.text,
        start: ov ? ov.start : cStart,
        end: ov ? ov.end : cEnd,
      });
      lastWhole = out[out.length - 1];
    }
  }
  // 新增的字卡：併進清單、依 start 排序（讓它出現在正確時間位置、預覽也吃得到）
  for (const nc of state.newCards) {
    out.push({
      c: null,
      newCard: nc,
      partIdx: null,
      key: `new:${nc.tempId}`,
      text: nc.text,
      start: nc.start,
      end: nc.end,
    });
  }
  out.sort((a, b) => a.start - b.start);
  return out;
}

// 時間軸還原：把畫面上的時間還原成磁碟 _v2.srt 的時間。必須跟 loadEpisodeState 的位移對稱：
//   載入：display = disk − audioSyncOffset + subtitleOffsetSec
//   存檔：disk = display + audioSyncOffset − subtitleOffsetSec
// 非破壞性字幕偏移是「顯示/合成層」的位移，不該被存進卡片磁碟時間；少減回去 → 拖一張卡存一次就漂一個偏移量。
// 沒外接音檔且偏移=0 → 原值回傳（no-op）。
function toDiskTime(t) {
  const off = (state.audioSyncOffset || 0) - (state.subtitleOffsetSec || 0);
  return {
    start: Math.round((t.start + off) * 100) / 100,
    end: Math.round((t.end + off) * 100) / 100,
  };
}

// 取一張卡實際生效的時間（含時間微調 override）
function getEffectiveCardTime(c) {
  const ov = state.timeOverrides.get(c.idx);
  return ov ? { start: ov.start, end: ov.end } : { start: c.start, end: c.end };
}

// 設一張卡的時間（夾範圍 + 四捨五入到 0.01s）；回到原值 → 移除 override
function setCardTime(c, start, end) {
  start = Math.max(0, Math.round(start * 100) / 100);
  end = Math.max(start + 0.1, Math.round(end * 100) / 100);
  const sameAsOrig =
    Math.abs(start - c.start) < 0.005 && Math.abs(end - c.end) < 0.005;
  const cur = state.timeOverrides.get(c.idx);
  if (sameAsOrig) {
    if (!cur) return;
    pushUndo();
    state.timeOverrides.delete(c.idx);
  } else {
    if (
      cur &&
      Math.abs(cur.start - start) < 0.005 &&
      Math.abs(cur.end - end) < 0.005
    ) {
      return;
    }
    pushUndo();
    state.timeOverrides.set(c.idx, { start, end });
  }
}

// 拖拉換位置：把 D 卡移到目標 T 卡的前 / 後 → 算落點時間（夾住不跟鄰居重疊）→ 設 override；
// expandedCards 依 start 重排，卡片就自動移到新位置（重用時間微調機制，不另搞排序狀態）。
function reorderCardTo(dragIdx, targetIdx, before) {
  if (dragIdx === targetIdx) return;
  const byIdx = new Map(state.cards.map((c) => [c.idx, c]));
  const D = byIdx.get(dragIdx);
  const T = byIdx.get(targetIdx);
  if (!D || !T) return;
  const tt = getEffectiveCardTime(T);
  const dt = getEffectiveCardTime(D);
  const dur = Math.max(0.3, Math.round((dt.end - dt.start) * 100) / 100);
  // 依生效時間排序的整卡清單（排除 D），夾住落點不跟前後鄰居重疊
  const others = state.cards
    .filter((c) => c.idx !== dragIdx)
    .map((c) => ({ idx: c.idx, ...getEffectiveCardTime(c) }))
    .sort((a, b) => a.start - b.start);
  const ti = others.findIndex((o) => o.idx === targetIdx);
  let ns, ne;
  if (before) {
    ne = tt.start;
    ns = ne - dur;
    const prev = ti > 0 ? others[ti - 1] : null;
    if (prev && ns < prev.end) ns = prev.end;
    if (ns < 0) ns = 0;
  } else {
    ns = tt.end;
    ne = ns + dur;
    const next = ti >= 0 && ti < others.length - 1 ? others[ti + 1] : null;
    if (next && ne > next.start) ne = next.start;
  }
  if (ne - ns < 0.1) ne = ns + 0.1;
  setCardTime(D, ns, ne); // 內含 pushUndo + 四捨五入 + clamp
  renderCards();
  renderCaption();
  renderTopbar();
}

// 時間微調的「目標」抽象：既有卡走 timeOverrides，新卡直接改自身 start/end。
// target = { domKey, get()->{start,end}, set(s,e), reset|null, isDirty()->bool }
function cardTimeTarget(c) {
  return {
    domKey: String(c.idx),
    get: () => getEffectiveCardTime(c),
    set: (s, e) => setCardTime(c, s, e),
    reset: () => {
      if (!state.timeOverrides.has(c.idx)) return;
      pushUndo();
      state.timeOverrides.delete(c.idx);
    },
    isDirty: () => state.timeOverrides.has(c.idx),
  };
}
function newCardTimeTarget(nc) {
  return {
    domKey: `new:${nc.tempId}`,
    get: () => ({ start: nc.start, end: nc.end }),
    set: (s, e) => {
      s = Math.max(0, Math.round(s * 100) / 100);
      e = Math.max(s + 0.1, Math.round(e * 100) / 100);
      if (Math.abs(nc.start - s) < 0.005 && Math.abs(nc.end - e) < 0.005)
        return;
      pushUndo();
      nc.start = s;
      nc.end = e;
    },
    reset: null,
    isDirty: () => false,
  };
}

// 時間微調工具列。按鈕只做 targeted DOM 更新，不整列 renderCards、不跳 scroll；
// undo / 整列重繪時靠 renderCards 的注入點還原。
function buildTimeToolbar(target) {
  const bar = document.createElement("div");
  bar.className = "card-time-edit";
  bar.addEventListener("click", (e) => e.stopPropagation());
  const val = document.createElement("span");
  val.className = "te-val";
  const repaint = () => {
    const t = target.get();
    val.textContent = `${t.start.toFixed(2)} → ${t.end.toFixed(2)}s`;
    const card = document.querySelector(
      `#cards-list .card[data-idx="${target.domKey}"]`,
    );
    if (card) {
      card.classList.toggle("time-dirty", target.isDirty());
      const cv = card.querySelector(".card-time-val");
      if (cv) cv.textContent = `${fmtTime(t.start)}\n${fmtTime(t.end)}`;
    }
    renderCaption();
    renderTopbar();
  };
  const act = (fn) => () => {
    fn();
    repaint();
  };
  const nudge = (ds, de) => {
    const t = target.get();
    target.set(t.start + ds, t.end + de);
  };
  const mk = (label, title, fn) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "te-btn";
    b.textContent = label;
    b.title = title;
    b.addEventListener("click", act(fn));
    return b;
  };
  const lab = (t) => {
    const s = document.createElement("span");
    s.className = "te-lab";
    s.textContent = t;
    return s;
  };
  bar.append(
    lab("起"),
    mk("⇤游標", "把起點設到目前播放位置", () => {
      const t = target.get();
      target.set($("#video").currentTime, t.end);
    }),
    mk("−", "起點 −0.1s", () => nudge(-0.1, 0)),
    mk("＋", "起點 +0.1s", () => nudge(0.1, 0)),
    lab("訖"),
    mk("游標⇥", "把終點設到目前播放位置", () => {
      const t = target.get();
      target.set(t.start, $("#video").currentTime);
    }),
    mk("−", "終點 −0.1s", () => nudge(0, -0.1)),
    mk("＋", "終點 +0.1s", () => nudge(0, 0.1)),
    val,
  );
  if (target.reset) {
    bar.append(mk("還原", "清除這張卡的時間微調", () => target.reset()));
  }
  const t0 = target.get();
  val.textContent = `${t0.start.toFixed(2)} → ${t0.end.toFixed(2)}s`;
  return bar;
}

// 開 / 關某卡的時間微調工具列（一次只開一張）
function toggleTimeEdit(target, cardEl) {
  if (state.timeEditKey !== null && state.timeEditKey !== target.domKey) {
    const prev = document.querySelector(
      `#cards-list .card[data-idx="${state.timeEditKey}"]`,
    );
    if (prev) {
      const bar = prev.querySelector(".card-time-edit");
      if (bar) bar.remove();
      prev.classList.remove("editing-time");
    }
  }
  const existing = cardEl.querySelector(".card-time-edit");
  if (existing) {
    existing.remove();
    cardEl.classList.remove("editing-time");
    state.timeEditKey = null;
  } else {
    cardEl.appendChild(buildTimeToolbar(target));
    cardEl.classList.add("editing-time");
    state.timeEditKey = target.domKey;
  }
}

// 渲染一張「新增字卡」列（自包，不碰既有卡的 sus / split / cam 邏輯）
function renderNewCardRow(r, list) {
  const nc = r.newCard;
  const div = document.createElement("div");
  div.className = "card card-new";
  div.dataset.idx = `new:${nc.tempId}`;

  const spacer = document.createElement("div"); // 對齊 grid 第一欄

  const time = document.createElement("div");
  time.className = "card-time";
  const timeVal = document.createElement("span");
  timeVal.className = "card-time-val";
  timeVal.style.whiteSpace = "pre";
  timeVal.textContent = `${fmtTime(r.start)}\n${fmtTime(r.end)}`;
  time.appendChild(timeVal);
  const badge = document.createElement("span");
  badge.className = "card-new-badge";
  badge.textContent = "新";
  time.appendChild(badge);
  const teBtn = document.createElement("button");
  teBtn.type = "button";
  teBtn.className = "card-time-edit-btn";
  teBtn.textContent = "⏱";
  teBtn.title = "調整這張新卡的時間（設到游標 / ±0.1s）";
  teBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleTimeEdit(newCardTimeTarget(nc), div);
  });
  time.appendChild(teBtn);
  time.addEventListener("click", () => {
    $("#video").currentTime = nc.start;
  });

  const text = document.createElement("div");
  text.className = "card-text";
  text.contentEditable = "true";
  text.textContent = nc.text;
  text.dataset.placeholder = "輸入字幕…";
  text.addEventListener("blur", () => {
    const v = text.textContent.trim();
    if (v === nc.text) return;
    pushUndo();
    nc.text = v;
    renderTopbar();
    renderCaption();
  });

  const del = document.createElement("button");
  del.className = "card-del";
  del.setAttribute("aria-label", "刪除這張新卡");
  del.innerHTML = window.Icons ? window.Icons.get("x", { size: 14 }) : "✕";
  del.addEventListener("click", () => {
    pushUndo();
    state.newCards = state.newCards.filter((x) => x.tempId !== nc.tempId);
    if (state.timeEditKey === `new:${nc.tempId}`) state.timeEditKey = null;
    renderCards();
    renderTopbar();
    renderCaption();
  });

  div.append(spacer, time, text, del);
  if (state.timeEditKey === `new:${nc.tempId}`) {
    div.appendChild(buildTimeToolbar(newCardTimeTarget(nc)));
    div.classList.add("editing-time");
  }
  list.appendChild(div);
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

// 講者顯示標籤：用數字 1/2/3，避免跟 A/B 鏡頭混淆。
// 內部 key 仍是 a/b/c（speakers.json、CSS 顏色 class speaker-a/b/c 都不動），只改「看到的字」。
// a→1 b→2 c→3 d→4…；非單字母 key 退回原樣大寫。
function speakerLabel(sp) {
  if (!sp) return "";
  return /^[a-z]$/.test(sp) ? String(sp.charCodeAt(0) - 96) : sp.toUpperCase();
}

// 算這張卡實際生效的鏡頭：往前找最近一張 explicit 標過的卡，沒有就回 "a"
// 注意：carry-forward 是依「展開後」的順序，不是 idx 大小（idx 不一定連續、
// 而且切過的卡會 carry 到自己的後續 sub-card）。
// 一次 O(n) carry-forward 把整列每張卡的「有效鏡頭」算進一張 Map，給整列共用。
// 語意等同舊版 computeEffectiveCamera 的「往前找最近一筆 explicit a/b、找不到當 a」：
// 順掃時維持 cur，遇 explicit 就更新，否則沿用 → 任一卡的 cur 即為其有效鏡頭。
// 取代逐卡各自 O(n) 回掃（整列渲染原本是 O(n²)）。
function buildEffectiveCameraMap(rendered) {
  const map = new Map();
  let cur = "a";
  for (const r of rendered) {
    const v = state.camerasMapping.get(r.key);
    if (v === "a" || v === "b") cur = v;
    map.set(r.key, cur);
  }
  return map;
}

// 單卡查詢（event-driven 的零星呼叫用，例如 timeupdate 疊 cam B overlay）。
// 熱路徑（整列渲染／ruler）請改用 buildEffectiveCameraMap 一次算好再查，勿逐卡呼叫此函式。
function computeEffectiveCamera(key) {
  return buildEffectiveCameraMap(expandedCards()).get(key) ?? "a";
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
  const all = expandedCards();
  // 有效鏡頭沿用「含已刪卡」的全列 carry-forward（與舊版 computeEffectiveCamera 一致），
  // 一次算好；ruler 本身只畫未刪段。
  const camMap = buildEffectiveCameraMap(all);
  const rendered = all.filter((r) => !state.deletions.has(r.key));
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
    const cam = camMap.get(r.key) ?? "a";
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

// === 字幕時間軸（拖卡片邊緣改進/出時間）===
// 整集時長映射成一條橫軸，每張字幕卡畫一塊 block；拖左/右邊緣改 start/end。
// edge-trim、不 ripple（只改自己；同源觸接子卡才同步相鄰邊界維持連續）。
const TL_MIN_DUR = 0.1; // 單句最短 0.1s，避免拖成零/負時長
let _tlDrag = null;

function _tlSetBlockGeom(el, start, end, t0, total) {
  el.style.left = `${((start - t0) / total) * 100}%`;
  // 寬度嚴格 ∝ 時長；極短卡的可見/可點下限改用 CSS min-width（像素級）。
  // 舊版用百分比下限（0.3% of total）會隨 zoom 一起放大，把幾乎所有卡夾成等寬。
  el.style.width = `${((end - start) / total) * 100}%`;
}

function renderCardTimeline() {
  const wrap = $("#card-timeline-wrap");
  const tl = $("#card-timeline");
  if (!wrap || !tl) return;
  const rendered =
    state.needsTranscribe || !state.cards.length ? [] : expandedCards();
  if (!rendered.length) {
    wrap.hidden = true;
    tl.innerHTML = "";
    return;
  }
  wrap.hidden = false;
  const t0 = rendered[0].start;
  const t1 = rendered[rendered.length - 1].end;
  const total = Math.max(t1 - t0, 0.001);
  tl.dataset.t0 = String(t0);
  tl.dataset.total = String(total);
  tl.innerHTML = "";
  for (const r of rendered) {
    const block = document.createElement("div");
    block.className = "tl-block";
    block.dataset.key = String(r.key);
    if (state.deletions.has(r.key)) block.classList.add("deleted");
    if (state.cardTimings.has(r.key)) block.classList.add("edited");
    _tlSetBlockGeom(block, r.start, r.end, t0, total);
    block.title = `${fmtTime(r.start)} – ${fmtTime(r.end)}（${(r.end - r.start).toFixed(1)}s）\n${r.text}`;
    block.addEventListener("click", (e) => {
      if (e.target.classList.contains("tl-handle")) return;
      $("#video").currentTime = r.start;
    });
    const hL = document.createElement("div");
    hL.className = "tl-handle tl-handle-l";
    hL.title = "拖曳改進場時間";
    hL.addEventListener("pointerdown", (e) => startTimelineDrag(e, r, "start"));
    const hR = document.createElement("div");
    hR.className = "tl-handle tl-handle-r";
    hR.title = "拖曳改出場時間";
    hR.addEventListener("pointerdown", (e) => startTimelineDrag(e, r, "end"));
    block.append(hL, hR);
    tl.appendChild(block);
  }
  // 波形層：後端 /api/waveform 算好的振幅輪廓，半透明蓋在字幕塊上。跟播放頭一樣每次
  // render 重建（tl.innerHTML 清空會清掉它），資料就緒後才畫出內容。
  const wfc = document.createElement("canvas");
  wfc.className = "tl-waveform";
  wfc.id = "tl-waveform";
  tl.appendChild(wfc);
  // 播放頭：跟著影片時間移動的豎線（updateTimelinePlayhead 每次 timeupdate 定位）
  const ph = document.createElement("div");
  ph.className = "tl-playhead";
  ph.id = "tl-playhead";
  tl.appendChild(ph);
  _applyTlZoomWidth();
  updateTimelinePlayhead($("#video").currentTime);
  drawTlWaveform(); // 若波形已載入就即刻畫；否則下面背景載入回來會再畫
  // 背景載波形（首次要 ffmpeg 解碼，可能 20~40s）：不 await、不擋首屏。只抓一次——
  // flag 擋住每次 render 重抓；換集會整頁重載、state 重置。轉錄前（沒卡）不抓。
  if (!state.waveform && !state._wfFetching && !state.needsTranscribe) {
    state._wfFetching = true;
    loadWaveform().finally(() => {
      state._wfFetching = false;
    });
  }
}

// 三邊同步：影片時間 → 時間軸播放頭位置 + 高亮當前 block（縮放時自動捲到可見）
function updateTimelinePlayhead(t) {
  const tl = $("#card-timeline");
  const ph = $("#tl-playhead");
  if (!tl || !ph) return;
  const t0 = parseFloat(tl.dataset.t0 || "0");
  const total = parseFloat(tl.dataset.total || "1");
  const pct = Math.max(0, Math.min(100, ((t - t0) / total) * 100));
  ph.style.left = `${pct}%`;
  // 縮放後（內層比視窗寬）→ 播放頭跑出可視範圍就自動捲動，維持在中間附近
  const scroll = tl.closest(".card-timeline-scroll");
  if (scroll && tl.offsetWidth > scroll.clientWidth + 1) {
    const x = (pct / 100) * tl.offsetWidth;
    if (
      x < scroll.scrollLeft + 40 ||
      x > scroll.scrollLeft + scroll.clientWidth - 40
    ) {
      scroll.scrollLeft = x - scroll.clientWidth / 2;
    }
  }
  // re-render 會重建 block → 當前高亮掉了就補回（_lastActiveKey 由 timeupdate 維護）
  if (_lastActiveKey != null && !tl.querySelector(".tl-block.playing")) {
    const blk = tl.querySelector(`.tl-block[data-key="${_lastActiveKey}"]`);
    if (blk) blk.classList.add("playing");
  }
}

// === 時間軸波形（Option B：後端 /api/waveform 一次算好 peaks + 靜音，前端只畫不算）===
// 硬條件：不影響檔案解析（後端只讀來源、落 04_工作檔/ 快取）、前端不卡（只在「載入完成／
// 縮放／視窗 resize」重畫，播放時完全不重畫——播放頭是獨立 DOM 用 CSS 移動）。
const TL_WAVE_MAX_PX = 16384; // canvas 背板單邊上限（WebKit 安全值）；超過改 CSS 拉伸，高倍率略糊但不空白

async function loadWaveform() {
  // 背景抓：首次要解碼可能久，失敗就當沒有，時間軸照常運作。抓回來才畫一次。
  try {
    const r = await fetch("/api/waveform", { cache: "no-store" });
    if (!r.ok) {
      state.waveform = null;
      return;
    }
    const data = await r.json();
    if (!data || !Array.isArray(data.peaks) || !data.peaks.length) {
      state.waveform = null;
      return;
    }
    state.waveform = data;
    drawTlWaveform();
  } catch (_) {
    state.waveform = null;
  }
}

function drawTlWaveform() {
  const tl = $("#card-timeline");
  const canvas = $("#tl-waveform");
  const wf = state.waveform;
  if (!tl || !canvas || !wf || !wf.peaks || !wf.peaks.length) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const t0 = parseFloat(tl.dataset.t0 || "0");
  const total = parseFloat(tl.dataset.total || "1");
  const cssW = tl.clientWidth;
  const cssH = tl.clientHeight;
  if (!(total > 0) || cssW < 2 || cssH < 2) return;
  // 背板尺寸吃 devicePixelRatio 求銳利，但封頂避免高倍率下超出 canvas 限制而整片空白
  const dpr = window.devicePixelRatio || 1;
  const backW = Math.min(Math.max(1, Math.round(cssW * dpr)), TL_WAVE_MAX_PX);
  const backH = Math.max(1, Math.round(cssH * dpr));
  if (canvas.width !== backW) canvas.width = backW;
  if (canvas.height !== backH) canvas.height = backH;
  ctx.clearRect(0, 0, backW, backH);

  const peaks = wf.peaks;
  const nP = peaks.length;
  const peakMax = wf.peak_max || 100;
  const secPerBucket = (wf.bucket_ms || 20) / 1000;
  // 每個背板欄位的高度：涵蓋多個 bucket → 取 max（縮小時）；不足一 bucket → 取最近（放大時，免斷點）
  const colV = new Float32Array(backW);
  for (let x = 0; x < backW; x++) {
    let k0 = Math.floor((t0 + (x / backW) * total) / secPerBucket);
    let k1 = Math.floor((t0 + ((x + 1) / backW) * total) / secPerBucket);
    let v = 0;
    if (k1 <= k0) {
      const k = k0 < 0 ? 0 : k0 >= nP ? nP - 1 : k0;
      v = peaks[k] || 0;
    } else {
      if (k0 < 0) k0 = 0;
      if (k1 > nP) k1 = nP;
      for (let k = k0; k < k1; k++) if (peaks[k] > v) v = peaks[k];
    }
    colV[x] = v;
  }

  // 中線鏡像的填色波形；半透明讓底下的字幕塊/高亮仍可讀
  const mid = backH / 2;
  const amp = backH * 0.46;
  ctx.beginPath();
  for (let x = 0; x < backW; x++) {
    const y = mid - (colV[x] / peakMax) * amp;
    if (x === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  for (let x = backW - 1; x >= 0; x--) {
    ctx.lineTo(x, mid + (colV[x] / peakMax) * amp);
  }
  ctx.closePath();
  const accent =
    getComputedStyle(document.documentElement)
      .getPropertyValue("--accent")
      .trim() || "#4a9eff";
  ctx.fillStyle = accent;
  ctx.globalAlpha = 0.4;
  ctx.fill();
  ctx.globalAlpha = 1;
}

// === 時間軸縮放（zoom + 橫向捲動）===
// #card-timeline 寬度 = zoom×100%（其容器 .card-timeline-scroll 提供橫向捲動）。
// 區塊仍用 left%/width% 定位 → 元素變寬時自動攤開，拖拉數學（吃 rect.width）不需改。
const TL_ZOOM_MIN = 1;
const TL_ZOOM_MAX = 60;
const TL_ZOOM_STEP = 1.6; // 每按一次 ＋/− 的倍率

function _applyTlZoomWidth() {
  const tl = $("#card-timeline");
  if (tl) tl.style.width = `${state.tlZoom * 100}%`;
  const out = $("#tl-zoom-out");
  const inn = $("#tl-zoom-in");
  const fit = $("#tl-zoom-fit");
  if (out) out.disabled = state.tlZoom <= TL_ZOOM_MIN + 1e-6;
  if (inn) inn.disabled = state.tlZoom >= TL_ZOOM_MAX - 1e-6;
  if (fit)
    fit.textContent =
      state.tlZoom > 1.01 ? `${state.tlZoom.toFixed(1)}×` : "適合";
}

// z：目標倍率（會 clamp）。anchorClientX：縮放錨點的螢幕 X（滑鼠位置）；
// 沒給就以視窗中央為錨。縮放後把錨點對應的時間點維持在原位，手感才穩。
function setTlZoom(z, { anchorClientX = null } = {}) {
  z = Math.max(TL_ZOOM_MIN, Math.min(TL_ZOOM_MAX, z));
  if (Math.abs(z - state.tlZoom) < 1e-6) return;
  const scroll = $("#card-timeline-scroll");
  const tl = $("#card-timeline");
  let frac = 0.5;
  let anchorOffset = scroll ? scroll.clientWidth / 2 : 0;
  if (scroll && tl) {
    const w = tl.offsetWidth || scroll.clientWidth || 1;
    anchorOffset =
      anchorClientX != null
        ? anchorClientX - scroll.getBoundingClientRect().left
        : scroll.clientWidth / 2;
    frac = (scroll.scrollLeft + anchorOffset) / w;
  }
  state.tlZoom = z;
  _applyTlZoomWidth();
  if (scroll && tl) {
    const newW = tl.offsetWidth || scroll.clientWidth * z;
    scroll.scrollLeft = frac * newW - anchorOffset;
  }
  drawTlWaveform(); // 寬度變了 → 波形背板重算重畫（只在縮放時，一次）
  try {
    localStorage.setItem("edit.tlZoom", String(z));
  } catch (_) {}
}

function startTimelineDrag(e, r, edge) {
  e.preventDefault();
  e.stopPropagation();
  const handle = e.currentTarget;
  const tl = $("#card-timeline");
  const rect = tl.getBoundingClientRect();
  pushUndo();
  _tlDrag = {
    key: r.key,
    edge,
    rect,
    total: Number(tl.dataset.total) || 1,
    t0: Number(tl.dataset.t0) || 0,
    tl,
    handle,
    block: handle.parentElement,
  };
  try {
    handle.setPointerCapture(e.pointerId);
  } catch (_) {}
  // 拖曳精確時間讀值：接到 body（fixed 定位，不受時間軸 overflow:hidden 裁切）
  let readout = document.getElementById("tl-drag-readout");
  if (!readout) {
    readout = document.createElement("div");
    readout.id = "tl-drag-readout";
    readout.className = "tl-drag-readout";
    document.body.appendChild(readout);
  }
  _tlDrag.readout = readout;
  handle.addEventListener("pointermove", onTimelineDragMove);
  handle.addEventListener("pointerup", endTimelineDrag);
  handle.addEventListener("pointercancel", endTimelineDrag);
}

function onTimelineDragMove(e) {
  if (!_tlDrag) return;
  const { rect, total, t0, edge, tl, block } = _tlDrag;
  const rendered = expandedCards();
  const i = rendered.findIndex((x) => String(x.key) === String(_tlDrag.key));
  if (i < 0) return;
  const cur = rendered[i];
  const prev = rendered[i - 1];
  const next = rendered[i + 1];
  let t = t0 + ((e.clientX - rect.left) / rect.width) * total;
  // 加分項：拖曳吸附靜音邊界（後端偵測的 silences）。按住 Alt 暫時關閉吸附。
  // 吸附半徑用像素換算（約 8px），縮放時手感一致；縮到很小則幾乎不吸附（本來也難精準對位）。
  let snapped = false;
  const sil = state.waveform && state.waveform.silences;
  if (!e.altKey && sil && sil.length) {
    const snapSec = Math.min(0.3, (8 / rect.width) * total);
    let bestD = snapSec;
    let best = null;
    for (const iv of sil) {
      // 拖 start 優先吸「靜音結束＝說話起點」；拖 end 優先吸「說話止＝靜音起點」
      const cands = edge === "start" ? [iv[1], iv[0]] : [iv[0], iv[1]];
      for (const b of cands) {
        const d = Math.abs(b - t);
        if (d < bestD) {
          bestD = d;
          best = b;
        }
      }
    }
    if (best != null) {
      t = best;
      snapped = true;
    }
  }
  let syncKey = null;
  let syncStart = 0;
  let syncEnd = 0;
  if (edge === "start") {
    // 同源觸接子卡：拖 start 同步把前一段 end 拉到同位，維持連續不留空窗
    const touch =
      prev && prev.c.idx === cur.c.idx && Math.abs(prev.end - cur.start) < 0.06;
    const lo = prev ? (touch ? prev.start + TL_MIN_DUR : prev.end) : t0;
    t = Math.max(lo, Math.min(cur.end - TL_MIN_DUR, t));
    state.cardTimings.set(cur.key, { start: t, end: cur.end });
    _tlSetBlockGeom(block, t, cur.end, t0, total);
    block.classList.add("edited");
    if (touch) {
      syncKey = prev.key;
      syncStart = prev.start;
      syncEnd = t;
    }
  } else {
    const touch =
      next && next.c.idx === cur.c.idx && Math.abs(next.start - cur.end) < 0.06;
    const hi = next ? (touch ? next.end - TL_MIN_DUR : next.start) : t0 + total;
    t = Math.max(cur.start + TL_MIN_DUR, Math.min(hi, t));
    state.cardTimings.set(cur.key, { start: cur.start, end: t });
    _tlSetBlockGeom(block, cur.start, t, t0, total);
    block.classList.add("edited");
    if (touch) {
      syncKey = next.key;
      syncStart = t;
      syncEnd = next.end;
    }
  }
  if (syncKey != null) {
    state.cardTimings.set(syncKey, { start: syncStart, end: syncEnd });
    const el = tl.querySelector(`.tl-block[data-key="${String(syncKey)}"]`);
    if (el) {
      _tlSetBlockGeom(el, syncStart, syncEnd, t0, total);
      el.classList.add("edited");
    }
  }
  // 拖曳讀值：跟游標顯示這一刻的精確時間（0.1s），吸附靜音時標注——直接回應「難判斷開始/結束點」
  if (_tlDrag.readout) {
    _tlDrag.readout.textContent = fmtTimeD(t) + (snapped ? "  ·吸附" : "");
    _tlDrag.readout.classList.toggle("snapped", snapped);
    _tlDrag.readout.style.left = `${e.clientX}px`;
    _tlDrag.readout.style.top = `${e.clientY}px`;
  }
}

function endTimelineDrag(e) {
  if (!_tlDrag) return;
  const { handle } = _tlDrag;
  try {
    handle.releasePointerCapture(e.pointerId);
  } catch (_) {}
  handle.removeEventListener("pointermove", onTimelineDragMove);
  handle.removeEventListener("pointerup", endTimelineDrag);
  handle.removeEventListener("pointercancel", endTimelineDrag);
  if (_tlDrag.readout) _tlDrag.readout.remove();
  _tlDrag = null;
  // 全量同步：卡片時間欄 / ruler / caption / 未儲存徽章 / timeline 重建
  rerenderEditState();
}

// 分軌集講者分布 ruler：同 cam-ruler，但用 speakers sidecar 染色（每 speaker 一色）
// 跟 cam ruler 的差異：speaker 沒 carry-forward；沒掛 speaker 的段不畫（避免被誤解成「預設講者」）
function renderSpeakerRuler() {
  const ruler = $("#speaker-ruler");
  if (!ruler) return;
  const showSpeakers =
    (state.mics && Object.keys(state.mics).length > 0) || state.hasSpeakerTags;
  if (!showSpeakers || !state.cards.length) {
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
    const label = s.sp ? `講者 ${speakerLabel(s.sp)}` : "（無 speaker）";
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
  // 有講者標（分軌 mics 或 Breeze speakers.json）→ 找所有 active 卡分行
  //   （兩人同時講話 → 上下兩行 + speaker 著色；分講者切卡的集多半每刻單卡 = 單行帶著色）
  // 純單軌無講者 → 退回單張卡的純文字（舊行為）
  const showTwoLine =
    (state.mics && Object.keys(state.mics).length > 0) || state.hasSpeakerTags;
  if (!showTwoLine) {
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

// resegment 待複查旗標原因 → 中文標籤（半句結尾 / 疑似重複幻覺）
function reviewReasonLabel(r) {
  return { half_sentence: "半句結尾", repetition: "疑似重複幻覺" }[r] || r;
}

// 「待複查卡」= resegment 旗標（半句 / 幻覺）或空拍卡。導覽 / 篩選共用這個判斷。
function cardNeedsReview(c) {
  return !!(c.needs_review || c.suspicious_pause);
}

function renderCards() {
  // T60：量測 renderCards 用時（搭配 .card 的 content-visibility）。
  // 若 cardCount > 500 且 dur > 50ms 就警告，當作導入 windowing 的訊號。
  const _t0 = performance.now();
  const list = $("#cards-list");
  list.innerHTML = "";
  list.classList.toggle("filter-review", state.reviewFilter);
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
  // 雙機集：一次 O(n) 算好整列有效鏡頭，迴圈內改 O(1) 查，取代逐卡兩次 O(n) 回掃
  const camMap = hasCamB ? buildEffectiveCameraMap(rendered) : null;
  // 效能：刪除鈕 icon 每卡一顆，先把兩種狀態的 SVG 各解析一次，之後逐卡 cloneNode
  // 取代逐卡 innerHTML 重新 parse（長集 1458 卡時這是建置迴圈的主要成本）
  let _delIconX = null;
  let _delIconUndo = null;
  if (window.Icons) {
    const _tmp = document.createElement("div");
    _tmp.innerHTML = window.Icons.get("x", { size: 14 });
    _delIconX = _tmp.firstChild;
    _tmp.innerHTML = window.Icons.get("rotate-ccw", { size: 14 });
    _delIconUndo = _tmp.firstChild;
  }
  // 效能：整列卡先進 DocumentFragment，最後一次性掛上 #cards-list，
  // 避免逐卡 appendChild 觸發 live-tree 重排
  const frag = document.createDocumentFragment();
  let prevRenderedRow = null; // 上一張 rendered row，給 Backspace 跨卡合併找合併目標
  for (const r of rendered) {
    const prevRow = prevRenderedRow; // per-iteration 快照，keydown 閉包用
    prevRenderedRow = r;
    if (r.newCard) {
      renderNewCardRow(r, frag);
      continue;
    }
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
        frag.appendChild(origin);
      }
      if (partIdx === parts.length - 1) div.classList.add("card-sub-last");
    }
    if (state.deletions.has(key)) div.classList.add("deleted");
    if (c.suspicious_pause && !isSub) div.classList.add("suspicious");
    // 待複查卡標記（給「只看待複查」篩選 + 導覽用）；sub-card 也標，沿用原卡旗標
    // 待複查卡：標過「看過」的掛 review-seen（淡化、退出篩選/導覽），否則 review-hit
    if (cardNeedsReview(c))
      div.classList.add(
        !isSub && state.reviewSeen.has(c.idx) ? "review-seen" : "review-hit",
      );
    // 雙機集：標記實際生效鏡頭，CSS 用 .card.cam-b 染左邊框
    if (hasCamB) {
      const eff = camMap.get(key) ?? "a";
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
    const timeVal = document.createElement("span");
    timeVal.className = "card-time-val";
    timeVal.style.whiteSpace = "pre";
    // 卡號（SRT idx）顯示在時間上方，方便對照外部清單／溝通「第幾卡」。
    // 切過的卡只在第一段顯示母卡號，其餘 part 留空，避免同一號重複印在每段。
    const showIdx = !isSub || partIdx === 0;
    timeVal.textContent =
      (showIdx ? `#${c.idx}\n` : "") + `${fmtTime(r.start)}\n${fmtTime(r.end)}`;
    time.appendChild(timeVal);
    time.addEventListener("click", () => {
      $("#video").currentTime = r.start;
    });
    if (!isSub && state.timeOverrides.has(c.idx))
      div.classList.add("time-dirty");
    // 未切的整卡才給「微調時間」入口 + 拖曳把手（切過的卡時間由斷句配速決定）
    if (!isSub) {
      // 拖曳換位置把手：拖一張卡 → 設時間 override 移到新位置。後端 always-sort 修正後，
      // 拖完存檔會「乾淨地」重排（不再像舊版那樣寫出非單調 SRT 整份亂掃）。
      const grip = document.createElement("span");
      grip.className = "card-grip";
      grip.textContent = "⠿";
      grip.title = "拖曳把這張卡移到新的時間位置";
      grip.draggable = true;
      time.insertBefore(grip, time.firstChild);

      const teBtn = document.createElement("button");
      teBtn.type = "button";
      teBtn.className = "card-time-edit-btn";
      teBtn.textContent = "⏱";
      teBtn.title = "微調這張卡的時間（設到游標 / ±0.1s）";
      teBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleTimeEdit(cardTimeTarget(c), div);
      });
      time.appendChild(teBtn);
    }
    // 分軌集才掛 speaker tag：值來自 srt_merge sidecar（read-only）
    // 要改 speaker → 走 _final_v2.speakers.json 手改或重跑 srt_merge，不在 UI 上 toggle
    const sp = computeEffectiveSpeaker(key);
    if (sp) {
      div.classList.add("card-has-speaker", `speaker-${sp}`);
      const tag = document.createElement("div");
      tag.className = `card-speaker-tag speaker-${sp}`;
      tag.textContent = speakerLabel(sp);
      tag.title = `講者 ${speakerLabel(sp)}（來自分軌 SRT，要改去 sidecar）`;
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
          warn.innerHTML =
            (window.Icons
              ? window.Icons.get("alert-triangle", { size: 10 })
              : "") + ` ${trailing.toFixed(1)}s`;
          warn.title = `斷句後尾段空窗 ${trailing.toFixed(1)} 秒沒分配字幕，可能漏字或斷句配速太快`;
          time.appendChild(warn);
        }
      }
    }
    // resegment 待複查旗標（半句結尾 / 重複幻覺）→ ⚠ icon 掛在時間欄，與其他
    // per-card 警告同欄。子卡不掛（複查判定走原始整卡，與 suspicious 一致）。
    if (c.needs_review && !isSub) {
      const reasons = c.review_reasons || [];
      const flag = document.createElement("div");
      flag.className = "card-review-flag";
      // 重複幻覺較重 → danger 紅；只有半句 → warning 黃
      if (reasons.includes("repetition")) flag.classList.add("severe");
      flag.title = `待複查：${reasons.map(reviewReasonLabel).join("、")}`;
      flag.innerHTML =
        (window.Icons ? window.Icons.get("alert-triangle", { size: 11 }) : "") +
        ` ${reasons.map(reviewReasonLabel).join("、")}`;
      time.appendChild(flag);
    }
    // 「看過」標記（session 內）：所有待複查卡（半句/幻覺 + 空拍）都可標，
    // 標過就淡化、退出待辦計數與 J/K 導覽。不寫檔、不進 undo、換集即清。
    if (cardNeedsReview(c) && !isSub) {
      const seen = state.reviewSeen.has(c.idx);
      const seenBtn = document.createElement("button");
      seenBtn.type = "button";
      seenBtn.className = "card-review-seen" + (seen ? " is-seen" : "");
      seenBtn.title = seen ? "已看過（點擊取消標記）" : "標記為已看過";
      seenBtn.innerHTML =
        (window.Icons ? window.Icons.get("check", { size: 11 }) : "✓") +
        (seen ? " 已看過" : " 看過");
      seenBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (state.reviewSeen.has(c.idx)) state.reviewSeen.delete(c.idx);
        else state.reviewSeen.add(c.idx);
        renderReviewToolbar();
        renderCards();
      });
      time.appendChild(seenBtn);
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
        mutateEditStateAtomic(() => {
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
          clearCardTimings(c.idx); // 合併改變段數 → 舊時間覆寫失效
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
        });
        return;
      }
      // Backspace at offset 0 on 整卡 → 併進「上一張整卡」（時間 = 上一張.start → 本卡.end）
      // 只支援整卡併整卡：本卡與上一張都必須未切（切過的卡文字在 cardSplits，後端合併掛不上去）。
      // 修飾鍵 / IME 組字中不攔，維持 cmd+Backspace 刪整行等原生行為。
      if (
        e.key === "Backspace" &&
        !e.shiftKey &&
        !e.metaKey &&
        !e.ctrlKey &&
        !e.altKey &&
        !e.isComposing &&
        !isSub &&
        prevRow &&
        !prevRow.newCard &&
        prevRow.partIdx == null &&
        prevRow.c &&
        getCursorOffset(text) === 0
      ) {
        e.preventDefault();
        const prev = prevRow.c;
        mutateEditStateAtomic(() => {
          const prevText = state.textOverrides.get(prev.idx) ?? prev.text;
          const curText = state.textOverrides.get(c.idx) ?? c.text;
          const mergedText = prevText + curText;
          // 合併後文字落在上一張整卡；等於原文就移除 override 保持乾淨
          if (mergedText && mergedText !== prev.text) {
            state.textOverrides.set(prev.idx, mergedText);
          } else {
            state.textOverrides.delete(prev.idx);
          }
          // 本卡併掉：進 merges、清掉自身所有 override（後端也會 fold，但前端要立即乾淨）
          state.cardMerges.add(c.idx);
          state.textOverrides.delete(c.idx);
          state.deletions.delete(c.idx);
          state.camerasMapping.delete(c.idx);
          state.speakersMapping.delete(c.idx);
          state.timeOverrides.delete(c.idx);
          clearCardTimings(c.idx);
          focusCardAt(prev.idx, prevText.length); // 游標停在兩卡接縫
        });
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
        mutateEditStateAtomic(() => {
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
          clearCardTimings(c.idx); // 切句改變段數 → 舊時間覆寫失效
          state.cardSplits.set(c.idx, [before, after]);
          focusSplitTarget(c.idx, 1);
        });
        return;
      }
      if (canSplit && isSub) {
        // 已切過的 sub-card：把 parts[partIdx] 拆 left/right、後面 composite key +1
        // 同人同句不會跨 sub-card 換鏡頭，cam 走 carry-forward 不必額外處理
        mutateEditStateAtomic(() => {
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
          clearCardTimings(c.idx); // 連鎖切句改變段數 → 舊時間覆寫失效
          state.cardSplits.set(c.idx, newParts);
          focusSplitTarget(c.idx, partIdx + 1);
        });
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
    const _delIcon = state.deletions.has(key) ? _delIconUndo : _delIconX;
    if (_delIcon) {
      del.appendChild(_delIcon.cloneNode(true));
    } else {
      del.textContent = state.deletions.has(key) ? "↺" : "✕";
    }
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
      const eff = camMap.get(key) ?? "a";
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
        // 把播放頭移到這張卡起點，預覽才會切到剛標的鏡頭
        //（refreshCamBOverlay 依 activeCardAt(currentTime) 判斷，不移就還停在別張卡）
        $("#video").currentTime = r.start;
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
        // 把播放頭移到這張卡起點，預覽才會切到剛標的 B 鏡頭
        //（refreshCamBOverlay 依 activeCardAt(currentTime) 判斷，不移就還停在別張卡）
        $("#video").currentTime = r.start;
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
    // 整列重繪（undo / 切句 / 刪除等）後，若這張卡的時間微調工具列原本開著就還原
    if (!isSub && state.timeEditKey === String(c.idx)) {
      div.appendChild(buildTimeToolbar(cardTimeTarget(c)));
      div.classList.add("editing-time");
    }
    frag.appendChild(div);
  }
  list.appendChild(frag);
  renderSusToolbar();
  renderReviewToolbar();
  renderCamRuler();
  renderSpeakerRuler();
  renderCardTimeline();
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
      !state.cardSplits.has(c.idx) &&
      !state.cardMerges.has(c.idx),
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
  // 已勾數 + 大約移除秒數預覽（實際因 cut_pad 略大，標「約」）
  const checkedEl = $("#sus-checked-count");
  if (checkedCount > 0) {
    const secs = checkedDeletionSeconds();
    checkedEl.textContent = `已勾 ${checkedCount}·約 ${secs.toFixed(1)} 秒`;
    checkedEl.title = "實際輸出因每側 cut_pad（0.15 秒）會略長於此預估值";
  } else {
    checkedEl.textContent = "已勾 0";
    checkedEl.title = "";
  }
  $("#sus-delete-checked").disabled = checkedCount === 0;

  // 「刪純反應詞」：只算 suspicious_reasons 命中 reaction_only 的紅卡，
  // 數量寫進鈕標、無此類卡時禁用
  const reactionCards = susCards.filter((c) =>
    (c.suspicious_reasons || []).includes("reaction_only"),
  );
  const reactBtn = $("#sus-delete-reactions");
  if (reactBtn) {
    reactBtn.disabled = reactionCards.length === 0;
    const reactLabel = reactBtn.querySelector("span:last-child");
    if (reactLabel)
      reactLabel.textContent =
        reactionCards.length > 0
          ? `刪純反應詞（${reactionCards.length}）`
          : "刪純反應詞";
  }

  // 全選按鈕：全勾就顯示「取消全選」反之顯示「全選紅卡」（用 icon 區分）
  const allChecked = susCards.length > 0 && checkedCount === susCards.length;
  const iconName = allChecked ? "check-square" : "square";
  const label = allChecked ? "取消全選" : "全選紅卡";
  $("#sus-select-all").innerHTML = window.Icons
    ? `${window.Icons.get(iconName, { size: 14 })}<span>${label}</span>`
    : label;
}

// 待複查卡導覽 toolbar：總數 / 只看待複查篩選 / 跳下一張
function renderReviewToolbar() {
  const bar = $("#review-toolbar");
  if (!bar) return;
  // 還沒刪除、也還沒標「看過」的原卡才算待辦（切過的卡旗標屬原句，仍保留導覽價值）
  const unseen = state.cards.filter(
    (c) =>
      cardNeedsReview(c) &&
      !state.deletions.has(c.idx) &&
      !state.reviewSeen.has(c.idx) &&
      !state.cardMerges.has(c.idx),
  );
  if (unseen.length === 0) {
    bar.classList.add("hidden");
    // 沒有待辦待複查卡時自動關掉篩選，避免畫面整片空
    if (state.reviewFilter) {
      state.reviewFilter = false;
      $("#cards-list").classList.remove("filter-review");
    }
    return;
  }
  bar.classList.remove("hidden");
  $("#review-count").textContent = unseen.length;
  const filterBtn = $("#review-filter");
  filterBtn.classList.toggle("active", state.reviewFilter);
  filterBtn.setAttribute("aria-pressed", state.reviewFilter ? "true" : "false");
}

// 從目前播放時間往後找下一張待複查卡，seek + scroll 過去（到尾端則繞回第一張）
function jumpToNextReview() {
  const v = $("#video");
  const hits = expandedCards().filter(
    (r) => cardNeedsReview(r.c) && !state.reviewSeen.has(r.c.idx),
  );
  if (!hits.length) return;
  const t = v.currentTime;
  const next = hits.find((r) => r.start > t + 0.05) || hits[0];
  v.currentTime = next.start;
  const el = document.querySelector(`.card[data-idx="${String(next.key)}"]`);
  if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
}

// 對稱 jumpToNextReview：往前找上一張待複查卡（到頭則繞回最後一張）
function jumpToPrevReview() {
  const v = $("#video");
  const hits = expandedCards().filter(
    (r) => cardNeedsReview(r.c) && !state.reviewSeen.has(r.c.idx),
  );
  if (!hits.length) return;
  const t = v.currentTime;
  const prev =
    [...hits].reverse().find((r) => r.start < t - 0.05) ||
    hits[hits.length - 1];
  v.currentTime = prev.start;
  const el = document.querySelector(`.card[data-idx="${String(prev.key)}"]`);
  if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
}

async function loadEpisodeState() {
  // 只重抓 episode + cards，重新轉字幕後會用到
  // cache:"no-store"：保險再加一層，避免瀏覽器吃舊 cache → 存檔後重載拿到存檔前資料
  // （後端 /api/episode 也補了 no-store header；雙保險）
  const r = await fetch("/api/episode", { cache: "no-store" });
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
  // 旋轉拉正（per cam）/ 節目封面開關 / 正片倍速
  const rot = data.rotate || {};
  state.rotate = { a: Number(rot.a) || 0, b: Number(rot.b) || 0 };
  state.coverEnabled = !!data.cover_enabled;
  // 預設 ON：episode.yaml 沒有 speed/silence_trim 欄位時，合成 YT 預設 1.15x 倍速 + 去空拍。
  // 只有明確寫 enabled:false 才關（respect explicit false）；避免舊集重新輸出時被迫加速。
  const sp = data.speed || {};
  state.speed = {
    enabled: sp.enabled !== undefined ? !!sp.enabled : true,
    factor: Number(sp.factor) || 1.15,
  };
  const stm = data.silence_trim || {};
  state.silenceTrim = {
    enabled: stm.enabled !== undefined ? !!stm.enabled : true,
    minSilence: Number(stm.min_silence) || 0.8,
  };
  state.outputDirty = false;
  if (typeof syncOutputControls === "function") syncOutputControls();
  state.deletions = new Set(data.deletions || []);
  state.cards = data.cards || [];
  state.textOverrides = new Map();
  state.susChecked = new Set();
  state.reviewSeen = new Set(); // 換集即清「看過」標記（session 內、不跨集）
  // 換集 / 重轉字幕：清掉舊集的切分記錄，避免 idx 對到新集不存在的卡或文字不符
  state.cardSplits = new Map();
  state.cardMerges = new Set();
  state.timeOverrides = new Map();
  state.timeEditKey = null;
  state.newCards = [];
  state.newCardSeq = 0;
  state.cardTimings = new Map();
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
  // 合法 speaker = mics 的 key ∪ speakers_mapping 實際出現的 value。
  // Breeze 集 mics 為空、但 speakers.json 有逐卡講者 → 靠後者放行（has_speaker_tags=true）。
  state.mics = data.mics || {};
  state.hasSpeakerTags = !!data.has_speaker_tags;
  state.cameraRule = data.camera_rule || {};
  const validSpeakers = new Set([
    ...Object.keys(state.mics),
    ...Object.values(data.speakers_mapping || {}),
  ]);
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
  // 拷貝一份（不共用 reels=yt 的同一物件），字級才能各調各的、互不影響
  state.subtitleStyleYt = data.subtitle_style
    ? { ...data.subtitle_style }
    : null;
  state.subtitleStyleReels = data.subtitle_style_reels
    ? { ...data.subtitle_style_reels }
    : data.subtitle_style
      ? { ...data.subtitle_style }
      : null;
  renderCaptionSizeControl();
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
  state.subtitleOffsetSec = Number(data.subtitle_offset_sec || 0);
  // 字幕時間軸總位移（與合成端 prepare_assembly 同邏輯，預覽才會跟輸出一致）：
  //   -audioSyncOffset：外接音檔比 cam A 慢 sync_offset 秒 → 字幕往前推對齊
  //   +subtitleOffsetSec：使用者設的非破壞性偏移（正值=字幕往後延）
  // 只動「顯示用」的 state.cards，不改磁碟 _v2.srt。
  const audioShift =
    state.audioPath && state.audioSyncOffset ? -state.audioSyncOffset : 0;
  const totalShift = audioShift + (state.subtitleOffsetSec || 0);
  if (Math.abs(totalShift) > 1e-6) {
    state.cards = state.cards
      .map((c) => ({
        ...c,
        start: Math.max(0, (c.start || 0) + totalShift),
        end: (c.end || 0) + totalShift,
      }))
      .filter((c) => c.end > 0);
  }
  // 字幕偏移：有字幕才顯示「偏移」入口（控制項收進 popover）；input 顯示目前已存的絕對偏移值
  const srtShiftToggle = $("#srt-shift-toggle");
  if (srtShiftToggle) srtShiftToggle.hidden = state.cards.length === 0;
  const srtShiftInput = $("#srt-shift-input");
  if (srtShiftInput && document.activeElement !== srtShiftInput) {
    srtShiftInput.value = state.subtitleOffsetSec
      ? String(state.subtitleOffsetSec)
      : "";
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
        !state.cardSplits.has(c.idx) &&
        !state.cardMerges.has(c.idx),
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
    const count = state.susChecked.size;
    const secs = checkedDeletionSeconds();
    // 大批量（≥10 張或約 >30 秒）刪除前二次確認；雖可 undo，仍加道閘擋誤刪整批
    if (
      (count >= 10 || secs > 30) &&
      !confirm(
        `確定刪除已勾的 ${count} 張紅卡？約移除 ${secs.toFixed(1)} 秒內容（可復原）。`,
      )
    ) {
      return;
    }
    pushUndo();
    for (const idx of state.susChecked) state.deletions.add(idx);
    state.susChecked.clear();
    renderCards();
    renderTopbar();
    renderCaption();
    renderTypo();
  });

  // 一鍵刪純反應詞：重算一次 reaction_only 紅卡（避免吃到過期快照），全刪
  $("#sus-delete-reactions").addEventListener("click", () => {
    const reactionCards = state.cards.filter(
      (c) =>
        c.suspicious_pause &&
        !state.deletions.has(c.idx) &&
        !state.cardSplits.has(c.idx) &&
        !state.cardMerges.has(c.idx) &&
        (c.suspicious_reasons || []).includes("reaction_only"),
    );
    if (reactionCards.length === 0) return;
    pushUndo();
    for (const c of reactionCards) state.deletions.add(c.idx);
    renderCards();
    renderTopbar();
    renderCaption();
    renderTypo();
  });

  // 待複查卡導覽：只看待複查篩選 + 跳下一張
  $("#review-filter").addEventListener("click", () => {
    state.reviewFilter = !state.reviewFilter;
    renderCards();
  });
  $("#review-prev").addEventListener("click", jumpToPrevReview);
  $("#review-next").addEventListener("click", jumpToNextReview);
}

function setupSrtShift() {
  $("#srt-shift-btn").addEventListener("click", async () => {
    const input = $("#srt-shift-input");
    // 絕對值語意：input 即「目前偏移」。空白 / 0 = 清除偏移（回原時間）。非破壞性：只存 yaml。
    const raw = input.value.trim();
    const offset = raw === "" ? 0 : Number(raw);
    if (!Number.isFinite(offset)) {
      alert("偏移秒數必須是數字（可正可負，0 = 清除）");
      return;
    }
    if (offset === (state.subtitleOffsetSec || 0)) {
      return; // 沒變更，免存
    }
    const btn = $("#srt-shift-btn");
    btn.disabled = true;
    try {
      // 走 /api/save 局部存檔（key-presence）：只寫 subtitle_offset_sec，不動 srt / 其他欄位
      const r = await fetch("/api/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subtitle_offset_sec: offset }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${r.status}`);
      }
      // 重載讓 preview 依新偏移重算顯示時間（磁碟 _v2.srt 維持原狀）
      await loadEpisodeState();
      renderCards();
      renderTopbar();
      renderCaption();
    } catch (e) {
      alert(`套用失敗：${e.message}`);
    } finally {
      btn.disabled = false;
    }
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
    if (state.cardMerges.has(c.idx)) continue; // 併掉的卡不算命中（已折進上一張）
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
// 連續被刪的卡併成整段（中間沒保留卡就跨停頓一起併），跟後端輸出一致 → 預覽不會播出碎片。
// 防 Whisper word-timestamp 把相鄰 cue end/start 排成重疊：刪除區間 end 不得吃進下一張未刪除卡，
// 否則播放會跳過明明沒被刪的卡（issue: 00:38-00:48 卡無故被預覽跳過）。
function deletionIntervals() {
  const cards = expandedCards();
  const raw = [];
  const kept = [];
  for (const r of cards) {
    if (state.deletions.has(r.key)) raw.push([r.start, r.end]);
    else kept.push([r.start, r.end]);
  }
  return mergeDeletionIntervals(raw, kept);
}

// 純函式：把 raw 刪除區間依「保留卡」邊界合併，回排序好、夾過界的整段。
// 從 deletionIntervals 抽出來，讓批刪秒數預覽（checkedDeletionSeconds）共用同一套合併規則。
// 連刪整段：重疊/貼著，或「中間間隙沒有保留卡」→ 併（跨停頓也併），跟後端輸出一致。
// 之前只併 gap<0.05 → 連刪兩張中間有真停頓就不併，預覽會把那段空檔播出來（碎片）。
function mergeDeletionIntervals(rawIntervals, keptIntervals) {
  const raw = [...rawIntervals].sort((a, b) => a[0] - b[0]);
  const kept = keptIntervals;
  const merged = [];
  for (const [s, e] of raw) {
    const last = merged[merged.length - 1];
    if (last) {
      const pe = last[1];
      const gapHasKept = kept.some(
        ([cs, ce]) => cs < s - 1e-6 && ce > pe + 1e-6,
      );
      if (s <= pe + 0.05 || !gapHasKept) {
        last[1] = Math.max(pe, e);
        continue;
      }
    }
    merged.push([s, e]);
  }
  // 夾到下一張保留卡起點（不吃進保留語音；處理 word_timestamp overlap）
  const keepStarts = kept.map(([s]) => s).sort((a, b) => a - b);
  for (const seg of merged) {
    const nextKeep = keepStarts.find((s) => s > seg[0] + 1e-6);
    if (nextKeep !== undefined && seg[1] > nextKeep) seg[1] = nextKeep;
  }
  return merged.filter((seg) => seg[1] > seg[0] + 0.01);
}

// 已勾紅卡批刪後「大約移除幾秒」— 給 toolbar 預覽 + 刪除前二次確認用。
// 用 mergeDeletionIntervals 跟實際刪除同套合併規則：已刪卡不算 raw 也不擋合併，
// 連續勾選跨停頓會併成整段。實際輸出因 cut_pad（每側 0.15s）會略大於此值，故標「約」。
function checkedDeletionSeconds() {
  const raw = [];
  const kept = [];
  for (const r of expandedCards()) {
    if (state.deletions.has(r.key)) continue; // 已刪：移出計算
    if (state.susChecked.has(r.key)) raw.push([r.start, r.end]);
    else kept.push([r.start, r.end]);
  }
  let total = 0;
  for (const [s, e] of mergeDeletionIntervals(raw, kept)) total += e - s;
  return total;
}

// 把 t 算到下一個 keep 區間的起點：在 deleted 區間內 → 跳到區間末端；
// 不在則回 t 本身。用於 play / timeupdate 時把預覽對齊到最終輸出時間軸。
// 守門：若 t 正落在某張保留卡的 [start, end) 內，一律不跳 — 處理 Whisper
// word_timestamp 把保留卡起點推到刪除卡之前的 overlap 情境（issue: 00:38-00:48 卡被跳）。
function nextKeepTime(t) {
  for (const r of expandedCards()) {
    if (!state.deletions.has(r.key) && t >= r.start && t < r.end) return t;
  }
  for (const [s, e] of deletionIntervals()) {
    if (t >= s && t < e) return e;
  }
  return t;
}

// 試聽該卡：P 鍵從某張卡 start 播到 end 就自動停。_auditionEnd 非 null = 試聽中。
let _auditionEnd = null;
function auditionCard(r) {
  const v = $("#video");
  _auditionEnd = r.end;
  v.currentTime = r.start;
  v.play().catch(() => {});
}

// 播放中若 currentTime 進入刪除區間 → 直接跳到區間末端，跟最終輸出體感一致。
// 暫停 / 拖 seek 時不踢，讓使用者還能進到刪除卡裡 inspect / 反悔復原。
function autoSkipDeletedSegments() {
  const v = $("#video");
  if (v.paused) return;
  if (_auditionEnd != null) return; // 試聽中不搶播放控制權
  const jumped = nextKeepTime(v.currentTime);
  if (jumped > v.currentTime + 0.01) v.currentTime = jumped;
}

// 上一次 highlight 的 card key — 只有真的換卡才動 .playing class 和 scrollIntoView，
// 避免 timeupdate（4-30Hz）一直疊 smooth scroll 動畫互打架造成卡片列表「亂跳」。
let _lastActiveKey = null;
$("#video").addEventListener("timeupdate", () => {
  autoPauseAtTailTrim();
  // 試聽該卡：播到卡尾就停（在 autoSkip 之前判，避免被刪除區間跳走）
  if (_auditionEnd != null && $("#video").currentTime >= _auditionEnd) {
    $("#video").pause();
    _auditionEnd = null;
  }
  autoSkipDeletedSegments();
  const t = $("#video").currentTime;
  const dur = $("#video").duration;
  const timeStr = `${fmtTime(t)} / ${fmtTime(dur)}`;
  $("#time").textContent = timeStr;
  const ctEl = $("#cards-time");
  if (ctEl) ctEl.textContent = timeStr; // 字幕區 sticky 列同步時間
  const seekEl = $("#seek");
  const pct = dur ? (t / dur) * 100 : 0;
  seekEl.value = pct;
  seekEl.style.setProperty("--seek-pct", `${pct}%`); // 已播進度填色

  const activeCard = activeCardAt(t);
  const activeKey = activeCard ? String(activeCard.key) : null;
  if (activeKey !== _lastActiveKey) {
    document
      .querySelectorAll(".card.playing, .tl-block.playing")
      .forEach((el) => el.classList.remove("playing"));
    if (activeKey != null) {
      const el = document.querySelector(`.card[data-idx="${activeKey}"]`);
      if (el) {
        el.classList.add("playing");
        // 正在編輯某張卡的文字時不自動捲走，否則邊播邊改會被一直拉到播放卡、打斷編輯
        const editing =
          document.activeElement?.classList?.contains("card-text");
        if (!editing)
          el.scrollIntoView({ block: "center", behavior: "smooth" });
      }
      // 時間軸：同步高亮當前 block（三邊同步）
      const blk = document.querySelector(`.tl-block[data-key="${activeKey}"]`);
      if (blk) blk.classList.add("playing");
    }
    _lastActiveKey = activeKey;
  }
  updateTimelinePlayhead(t); // 播放頭每幀都動（不只換卡時）
  renderCaption();
});

const playBtn = $("#play-btn");
// 播放/暫停切換：給 play 按鈕與 Space 快捷鍵共用
function togglePlay() {
  const v = $("#video");
  _auditionEnd = null; // 使用者自行播放/暫停 → 取消試聽守門
  if (v.paused) v.play();
  else v.pause();
}
playBtn.addEventListener("click", togglePlay);
// 字幕區 sticky 列的播放鈕：捲到字幕下方也能就近播放/暫停
$("#cards-play-btn")?.addEventListener("click", togglePlay);

// 切到上一張 / 下一張字幕卡（dir = -1 / +1）：seek 影片到該卡 start。
// .playing 高亮 + scrollIntoView 由 #video 的 timeupdate（seek 也會 fire）統一處理。
function stepCard(dir) {
  const v = $("#video");
  _auditionEnd = null; // 切換卡片 → 取消試聽守門
  const rendered = expandedCards();
  if (!rendered.length) return;
  const t = v.currentTime;
  let idx = rendered.findIndex((r) => t >= r.start && t < r.end);
  if (idx < 0) {
    // 不在任何卡內（落在空窗）：以第一張 start > t 的卡為「下一張」基準
    const nextIdx = rendered.findIndex((r) => r.start > t);
    if (dir > 0) {
      idx = nextIdx < 0 ? rendered.length - 1 : nextIdx;
    } else {
      idx = nextIdx < 0 ? rendered.length - 1 : Math.max(0, nextIdx - 1);
    }
  } else {
    idx = Math.max(0, Math.min(rendered.length - 1, idx + dir));
  }
  const target = rendered[idx];
  v.currentTime = target.start;
  const el = document.querySelector(`.card[data-idx="${String(target.key)}"]`);
  if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
}

// 由影片事件統一更新圖示，避免 click handler 與程式化 play/pause 不同步
function setPlayIcon(name) {
  const html = window.Icons
    ? window.Icons.get(name, { size: 16 })
    : name === "pause"
      ? "⏸"
      : "▶";
  const label = name === "pause" ? "暫停" : "播放";
  for (const b of [playBtn, $("#cards-play-btn")]) {
    if (!b) continue;
    b.innerHTML = html;
    b.setAttribute("aria-label", label);
  }
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
  _auditionEnd = null; // 手動拖時間軸 → 取消試聽守門
  if (v.duration) v.currentTime = (e.target.value / 100) * v.duration;
  e.target.style.setProperty("--seek-pct", `${e.target.value}%`);
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

// 字幕時間軸縮放：還原上次倍率 + 綁 −/適合/＋ 與 Ctrl/⌘+滾輪
(function initTlZoom() {
  const v = parseFloat(localStorage.getItem("edit.tlZoom"));
  if (v >= TL_ZOOM_MIN && v <= TL_ZOOM_MAX) state.tlZoom = v;
})();
$("#tl-zoom-in")?.addEventListener("click", () =>
  setTlZoom(state.tlZoom * TL_ZOOM_STEP),
);
$("#tl-zoom-out")?.addEventListener("click", () =>
  setTlZoom(state.tlZoom / TL_ZOOM_STEP),
);
$("#tl-zoom-fit")?.addEventListener("click", () => setTlZoom(1));
$("#card-timeline-scroll")?.addEventListener(
  "wheel",
  (e) => {
    // 只有按住 Ctrl/⌘ 才縮放；否則維持瀏覽器原生橫向捲動
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    setTlZoom(state.tlZoom * factor, { anchorClientX: e.clientX });
  },
  { passive: false },
);

// 視窗縮放 → 時間軸寬度改變 → 波形背板需重算重畫（rAF 節流，連續 resize 只畫最後一次）
let _wfResizeRaf = null;
window.addEventListener("resize", () => {
  if (_wfResizeRaf != null) return;
  _wfResizeRaf = requestAnimationFrame(() => {
    _wfResizeRaf = null;
    drawTlWaveform();
  });
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

// 拖曳 trim handle：pointerdown 在拖把上 → rAF 節流更新 + tooltip + 畫格預覽 → pointerup 收工。
// pushUndo 只在第一次 move 時押一次，避免拖一下噴一堆 history。
// 點下去沒拖 = 沒進 stack，跟 frame drag / resize 同 pattern。
function startTrimDrag(kind, downEvent) {
  const v = $("#video");
  const dur = v.duration || 0;
  if (!dur) return;
  const handle =
    kind === "head" ? $("#trim-handle-head") : $("#trim-handle-tail");
  handle.classList.add("dragging");
  // pointer capture：拖出視窗 / 觸控都不會掉拖；autoSkip/autoPause 有 paused 防護，
  // 先暫停讓畫格預覽穩定（拖完留在原地，使用者正好檢視切點畫面）
  if (downEvent && downEvent.pointerId != null && handle.setPointerCapture) {
    try {
      handle.setPointerCapture(downEvent.pointerId);
    } catch (_) {}
  }
  if (!v.paused) v.pause();
  const wrap = $(".seek-wrap");
  const rect = wrap.getBoundingClientRect();
  const tooltip = $("#trim-tooltip");
  let pushed = false;
  let pendingX = null;
  let rafId = null;

  // mousemove 可達 60+Hz，全部進 rAF 收斂成每幀最多一次 state+DOM 更新
  const apply = () => {
    rafId = null;
    if (pendingX == null) return;
    const x = Math.max(0, Math.min(rect.width, pendingX - rect.left));
    pendingX = null;
    const sec = (x / rect.width) * dur;
    // clamp：頭不能超過尾的對面；尾同理；最少留 0.5s 內容免得整段被吃光
    const MIN_REMAIN = 0.5;
    let posSec; // 拖把所在的絕對時間（tooltip 定位 + 畫格預覽用）
    if (kind === "head") {
      const maxHead = dur - (state.tailTrimSec || 0) - MIN_REMAIN;
      const next = Math.round(Math.max(0, Math.min(sec, maxHead)) * 10) / 10;
      posSec = next;
      if (next !== state.headTrimSec) {
        if (!pushed) {
          pushUndo();
          pushed = true;
        }
        state.headTrimSec = next;
        renderTrimControls();
        renderTopbar();
      }
      tooltip.textContent = `片頭 ${fmtTimeD(next)}`;
    } else {
      const tailFromEnd = dur - sec;
      const maxTail = dur - (state.headTrimSec || 0) - MIN_REMAIN;
      const next =
        Math.round(Math.max(0, Math.min(tailFromEnd, maxTail)) * 10) / 10;
      posSec = dur - next;
      if (next !== state.tailTrimSec) {
        if (!pushed) {
          pushUndo();
          pushed = true;
        }
        state.tailTrimSec = next;
        renderTrimControls();
        renderTopbar();
      }
      tooltip.textContent = `片尾 -${next.toFixed(1)}s（${fmtTimeD(posSec)}）`;
    }
    // tooltip 跟著拖把走 + 影片即時跳到拖把位置（所見即切點畫格）
    tooltip.style.left = `${((posSec / dur) * 100).toFixed(2)}%`;
    tooltip.hidden = false;
    if (typeof v.fastSeek === "function") v.fastSeek(posSec);
    else v.currentTime = posSec;
  };

  const onMove = (e) => {
    pendingX = e.clientX;
    if (rafId == null) rafId = requestAnimationFrame(apply);
  };
  const onUp = () => {
    handle.classList.remove("dragging");
    tooltip.hidden = true;
    if (rafId != null) cancelAnimationFrame(rafId);
    document.removeEventListener("pointermove", onMove);
    document.removeEventListener("pointerup", onUp);
    document.removeEventListener("pointercancel", onUp);
  };
  document.addEventListener("pointermove", onMove);
  document.addEventListener("pointerup", onUp);
  document.addEventListener("pointercancel", onUp);
  // 按下當下就先畫一次：點一下（不拖）也立即看到 tooltip + 畫格
  if (downEvent) {
    pendingX = downEvent.clientX;
    apply();
  }
}

$("#trim-handle-head").addEventListener("pointerdown", (e) => {
  e.preventDefault();
  startTrimDrag("head", e);
});
$("#trim-handle-tail").addEventListener("pointerdown", (e) => {
  e.preventDefault();
  startTrimDrag("tail", e);
});

// 鍵盤微調：focus 在把手上時 ← → 走 0.1s、Shift+← → 走 0.5s、Cmd/Ctrl+← → 走 1s。
// 拖曳精度受 seek bar 像素 / 影片時長比例限制（30 min 在 600px ≈ 3s/px），鍵盤是唯一可以 ±0.1s 精準的路徑。
function nudgeTrim(kind, deltaSec) {
  const v = $("#video");
  const dur = v.duration || 0;
  if (!dur) return;
  const MIN_REMAIN = 0.5;
  pushUndo();
  if (kind === "head") {
    const maxHead = dur - (state.tailTrimSec || 0) - MIN_REMAIN;
    const next = Math.max(0, Math.min(state.headTrimSec + deltaSec, maxHead));
    state.headTrimSec = Math.round(next * 10) / 10;
  } else {
    const maxTail = dur - (state.headTrimSec || 0) - MIN_REMAIN;
    const next = Math.max(0, Math.min(state.tailTrimSec + deltaSec, maxTail));
    state.tailTrimSec = Math.round(next * 10) / 10;
  }
  renderTrimControls();
  renderTopbar();
}
function attachTrimKeyboardNudge(handleId, kind) {
  $(handleId).addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const step = e.metaKey || e.ctrlKey ? 1.0 : e.shiftKey ? 0.5 : 0.1;
    const sign = e.key === "ArrowLeft" ? -1 : 1;
    nudgeTrim(kind, step * sign);
  });
}
attachTrimKeyboardNudge("#trim-handle-head", "head");
attachTrimKeyboardNudge("#trim-handle-tail", "tail");

// 從片頭起點播放（不是當下位置）— 預覽頭尾切完的效果
$("#trim-play-head").addEventListener("click", () => {
  const v = $("#video");
  const dur = v.duration || 0;
  if (!dur) return;
  v.currentTime = state.headTrimSec || 0;
  v.play().catch(() => {});
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

  // 還原上次狀態：預設收合（HTML 已帶 collapsed），只有使用者上次手動展開過（"0"）才打開
  try {
    const savedTab = localStorage.getItem(KEY_TAB);
    if (savedTab) showTab(savedTab);
    if (localStorage.getItem(KEY_COLLAPSED) === "0") {
      drawer.classList.remove("collapsed");
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
      // 切版本 → 字幕風格/字級隨之改變：重算預覽字體 + 更新字級控制顯示
      applyCaptionFontSize();
      renderCaptionSizeControl();
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

// 主儲存鈕與「合成設定 → 開始合成」共用的 /api/save payload 序列化。
// 抽出來讓「合成的下一步」能用同一條已驗證的存檔路徑把倍速等輸出設定寫進 episode.yaml。
function buildSavePayload({ withSpeed = false } = {}) {
  const payload = {
    crop_yt: serializeCropForSave(state.cropYt, state.cropYtB),
    crop_reels: serializeCropForSave(state.cropReels, state.cropReelsB),
    // 旋轉拉正（per cam 度數）/ 節目封面開關；後端 key-presence 判斷要不要寫
    rotate: { a: state.rotate.a, b: state.rotate.b },
    cover_enabled: state.coverEnabled,
    // 字幕字級：只送 font_size，後端跟 defaults 比對 → 等於預設就移除 override、保持 yaml 乾淨
    subtitle_style: {
      font_size: Number(state.subtitleStyleYt?.font_size) || null,
    },
    subtitle_style_reels: {
      font_size: Number(state.subtitleStyleReels?.font_size) || null,
    },
    silence_trim: {
      enabled: state.silenceTrim.enabled,
      min_silence: state.silenceTrim.minSilence,
    },
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
    // 跨卡合併：[old_idx, ...]；後端把這些卡從 SRT 拿掉、結束時間接到上一張整卡。
    // 合併後文字由上面的 cards（textOverrides）落在上一張卡，避免重複串接。
    merges: [...state.cardMerges],
    // 單卡時間微調：{ "<idx>": {start, end} }；後端 serialize 前覆寫該卡 start/end
    // ── 時間軸還原 ──
    // 載入時（loadEpisodeState）字卡被整批 -audioSyncOffset 移到 cam A 軸（讓播放預覽的字幕
    // highlight 對得上 video.currentTime）。但磁碟 _v2.srt 是「外接音檔時間軸」，存檔端
    // 必須把使用者在 cam A 軸上微調出來的 start/end 加回 +audioSyncOffset 還原成外接音檔軸，
    // 才能跟磁碟一致、避免每次「存→重載」都再被減一次 offset（症狀：時間越存越早、修不回）。
    time_overrides: Object.fromEntries(
      [...state.timeOverrides.entries()].map(([idx, t]) => [
        idx,
        toDiskTime(t),
      ]),
    ),
    // 新增字卡：[{start, end, text}]；後端 append 進 SRT、依時間排序重編號
    // 新卡 start/end 來自 video.currentTime（cam A 軸），同樣要 +audioSyncOffset 還原成磁碟軸
    new_cards: state.newCards.map((c) => ({
      ...toDiskTime(c),
      text: c.text,
    })),
    // 時間軸拖拉改的字幕時間：composite key → {start, end}；同 +audioSyncOffset 還原磁碟軸
    card_timings: Object.fromEntries(
      [...state.cardTimings.entries()].map(([k, t]) => [k, toDiskTime(t)]),
    ),
    // Reels 片段：list of {name, start_card, end_card}；空 list 後端會把 key 砍掉
    reels_clips: state.reelsClips.map((c) => ({
      name: c.name,
      start_card: c.start_card,
      end_card: c.end_card,
    })),
  };
  // 倍速只在「合成設定 modal」→「開始合成」時送（withSpeed=true）；改字卡的主存檔不送。
  // 否則 state.speed.enabled 一旦 stale 成 false，改個字卡存檔就無聲無息把 episode.yaml 的
  // speed 洗掉 → 影片變回原速（曾導致 53 分災難）。要關閉倍速請在合成 modal 取消勾選。
  if (withSpeed) {
    payload.speed = {
      enabled: state.speed.enabled,
      factor: state.speed.factor,
    };
  }
  return payload;
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
  const payload = buildSavePayload();
  try {
    await postSave(payload);
    setSaveBtnLabel("check", "已儲存");
    // 儲存成功後既有的 undo 紀錄已落地，視為起點 → 清空 stacks
    clearUndoStacks();
    // 切卡 bug 修復：存檔已把 splits / overrides 寫進 _v2.srt（卡片切開、重編號、變多）。
    // 不重新載入的話 state.cards / state.cardSplits 會停在舊狀態，下次存檔時舊的 cardSplits
    // 會套到已經錯位的卡上 → 卡片重複 / 錯亂（先前「同句連續 N 張」的根因）。
    // loadEpisodeState 重抓 /api/episode 並清空 cardSplits / textOverrides，讓下一輪從乾淨狀態開始。
    await loadEpisodeState();
    renderCards();
    // 引導使用者按合成（兩個版本都高亮，使用者自行挑要先做哪一個）
    const ytBtn = $("#assemble-yt-btn");
    const reelsBtn = $("#assemble-reels-btn");
    // 合成鈕已收進「輸出」下拉，存檔後自動展開讓引導用的 pulse 看得到
    $("#output-menu-btn")?._popover?.open();
    ytBtn?.classList.add("pulse");
    reelsBtn?.classList.add("pulse");
    ytBtn?.scrollIntoView({ block: "nearest", inline: "nearest" });
    setTimeout(() => {
      ytBtn?.classList.remove("pulse");
      reelsBtn?.classList.remove("pulse");
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
  } else if (f.path.toLowerCase().endsWith(".srt") && !f.is_main_srt_backup) {
    // 自帶字幕：直接拿這份 .srt 跑斷句 + 改錯字 + 反幻覺（不跑雲端 STT）
    action = document.createElement("button");
    action.className = "file-stt";
    action.innerHTML = `${iconHtml("scissors", 12)}<span>斷句</span>`;
    action.title =
      "用這份字幕重新斷句 + 改錯字 + 反幻覺（不跑雲端 STT），覆蓋 _v2.srt";
    action.addEventListener("click", () => requestResegment(f.path));
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

// 自帶字幕：只跑 resegment 後處理（斷句 + 改錯字 + 反幻覺），不跑雲端 STT。
// 複用 transcribe-modal 的版面；resegment 是同步秒級，不需要 phase pills / poll。
function requestResegment(srcPath) {
  $("#transcribe-progress").hidden = true;
  const go = $("#transcribe-go");
  const cancel = $("#transcribe-cancel");
  go.hidden = false;
  go.disabled = false;
  cancel.hidden = false;
  cancel.disabled = false;
  cancel.textContent = "取消";
  setModalStatusTitle(
    "transcribe-title",
    "scissors",
    "重新斷句（不轉 STT）",
    "accent",
  );
  $("#transcribe-msg").innerHTML =
    `來源字幕：<code>${srcPath}</code><br><br>` +
    `直接用這份字幕重新斷句 + 改錯字 + 反幻覺，覆寫 <code>_v2.srt</code>，<strong>不會呼叫雲端 STT</strong>。<br>` +
    `原稿會先自動備份成 <code>.bak.srt</code>。`;
  go.textContent = "開始斷句";
  go.onclick = () => runResegment(srcPath);
  cancel.onclick = () => hideModal("transcribe-modal");
  showModal("transcribe-modal");
}

async function runResegment(srcPath) {
  setModalStatusTitle("transcribe-title", "scissors", "重新斷句中…", "accent");
  $("#transcribe-msg").innerHTML =
    `<div class="modal-loading"><span class="spinner"></span> 正在重新斷句 + 改錯字…</div>`;
  $("#transcribe-progress").hidden = true;
  const go = $("#transcribe-go");
  const cancel = $("#transcribe-cancel");
  go.disabled = true;
  cancel.disabled = true;
  try {
    const r = await fetch("/api/resegment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(srcPath ? { src_srt: srcPath } : {}),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    await finishTranscribe({ ok: true, out_srt: body.out_srt });
  } catch (e) {
    finishTranscribe({ ok: false, error: e.message });
  }
}

// 三段進度條：每段佔總長 1/3
// 後端依 provider 送不同細粒度 phase：
//   雲端 gemini/openai：compress → upload → resegment
//   本地 whisper_mlx：compress → vad → decode → stt → resegment
// UI 只有三顆 pill（壓縮 / STT / 切句），把細 phase 收斂到三段桶；
// 桶決定 pill 高亮與整體百分比，細 phase 名只用在文字 label。
// 漏掉任何細 phase → computeOverallPercent 的 indexOf 回 -1 → 進度條卡 0%。
const TRANSCRIBE_PHASES = ["compress", "upload", "resegment"];
const TRANSCRIBE_PHASE_BUCKET = {
  compress: "compress",
  vad: "upload",
  decode: "upload",
  stt: "upload",
  upload: "upload",
  resegment: "resegment",
  // 一鍵 Breeze：三段對到三桶（pills 會在 breeze 時隱藏，只用桶推進度條 %）
  "breeze-asr": "compress",
  ingest: "upload",
  proofread: "resegment",
};
const bucketOfPhase = (phase) => TRANSCRIBE_PHASE_BUCKET[phase] || phase;
const TRANSCRIBE_PHASE_LABELS = {
  compress: "壓縮音檔",
  vad: "偵測語音段",
  decode: "載入模型",
  stt: "語音辨識中",
  upload: "上傳並等待 STT",
  resegment: "重新切句",
  "breeze-asr": "Breeze 轉錄中（最久，請耐心等，勿關分頁）",
  ingest: "匯入字幕（去講者標籤 → 講者表）",
  proofread: "本地校對（同音字／術語）",
};
let _transcribePollTimer = null;

function stopTranscribePoll() {
  if (_transcribePollTimer) {
    clearInterval(_transcribePollTimer);
    _transcribePollTimer = null;
  }
}

// 輪詢 transcribe status 直到離開 running（或 timeout）。回傳最終 state。
// 後端 cancel 已同步等收尾完才回；這是取消請求網路失敗時的保險。
async function pollUntilTranscribeIdle(timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  let last = "idle";
  while (Date.now() < deadline) {
    try {
      const r = await fetch("/api/transcribe/status");
      const s = await r.json();
      last = s.state;
      if (s.state !== "running") return s.state;
    } catch (e) {
      /* 暫時失敗，續試 */
    }
    await new Promise((res) => setTimeout(res, 200));
  }
  return last;
}

// 打 /api/transcribe/cancel 並確保後端已離開 running（single / breeze / per-mic 共用）
async function requestTranscribeCancel() {
  let finalState = "running";
  try {
    const r = await fetch("/api/transcribe/cancel", { method: "POST" });
    const d = await r.json().catch(() => ({}));
    finalState = d.state || "running";
  } catch (e) {
    /* 取消請求失敗：改用輪詢確認後端狀態 */
  }
  if (finalState === "running") {
    finalState = await pollUntilTranscribeIdle();
  }
  return finalState;
}

// 取消後把轉錄 modal 收回「可重轉」狀態（重開 modal 時 requestTranscribe 會重設其餘欄位）
function resetTranscribeModalAfterCancel() {
  const go = $("#transcribe-go");
  const cancel = $("#transcribe-cancel");
  hideModal("transcribe-modal");
  $("#transcribe-progress").hidden = true;
  if (go) {
    go.hidden = false;
    go.disabled = false;
  }
  if (cancel) {
    cancel.disabled = false;
    cancel.textContent = "取消";
    cancel.onclick = () => hideModal("transcribe-modal");
  }
}

// 轉錄中按「取消轉錄」（single / breeze 共用 transcribe modal）：
// 按鈕禁用防連點 → 停 poll → 通知後端砍 job 並等收尾 → 收回可重轉狀態
async function abortTranscribeFromModal() {
  const cancel = $("#transcribe-cancel");
  if (cancel) {
    cancel.disabled = true; // async 期間禁用
    cancel.textContent = "取消中…";
  }
  stopTranscribePoll(); // 停進度 poll，避免和取消狀態打架
  $("#transcribe-phase-label").textContent = "取消中…";
  await requestTranscribeCancel();
  resetTranscribeModalAfterCancel();
}

// 取消後把分軌 modal 收回「可重轉」狀態（重開時 openPerMicTranscribe 會整組重設）
function resetPerMicModalAfterCancel() {
  const go = $("#per-mic-go");
  const cancel = $("#per-mic-cancel");
  hideModal("per-mic-modal");
  if (go) {
    go.hidden = false;
    go.disabled = false;
  }
  if (cancel) {
    cancel.disabled = false;
    cancel.textContent = "取消";
    cancel.onclick = () => hideModal("per-mic-modal");
  }
}

// 分軌轉錄中按「取消」：流程同 abortTranscribeFromModal，操作 per-mic modal
async function abortPerMicFromModal() {
  const cancel = $("#per-mic-cancel");
  if (cancel) {
    cancel.disabled = true;
    cancel.textContent = "取消中…";
  }
  stopPerMicPoll();
  $("#per-mic-phase-label").textContent = "取消中…";
  await requestTranscribeCancel();
  resetPerMicModalAfterCancel();
}

function renderTranscribePhasePills(currentPhase, state) {
  // pending / active / done 三種狀態，依目前 phase 與 state 推導
  const curIdx = TRANSCRIBE_PHASES.indexOf(bucketOfPhase(currentPhase));
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
  const idx = TRANSCRIBE_PHASES.indexOf(bucketOfPhase(phase));
  if (idx < 0) return 0;
  return idx * (100 / 3) + Math.max(0, Math.min(100, percent)) / 3;
}

// 一鍵 Breeze 的進度配重：ASR（breeze-asr）吃掉真實時間的絕大多數，讓它佔進度條主體，
// 尾段兩個快 phase（匯入／校對）只補尾巴 → 條的移動貼近實際等待，不會「爬到 33% 再瞬跳」。
const BREEZE_PHASE_SPAN = {
  "breeze-asr": [0, 92],
  ingest: [92, 96],
  proofread: [96, 99],
};
function computeBreezeOverallPercent(phase, percent) {
  const span = BREEZE_PHASE_SPAN[phase];
  if (!span) return 0;
  const [lo, hi] = span;
  const p = Math.max(0, Math.min(100, percent)) / 100;
  return lo + (hi - lo) * p;
}

// 秒數 →「M:SS」，給經過時間計時器用（模型載入期還沒 tqdm 時也讓使用者看到有在動）。
function fmtElapsed(sec) {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}
let _breezeStartMs = 0;

// 一鍵 Breeze 轉字幕：Breeze ASR → 匯入講者 → 校對，整條龍背景跑，共用 transcribe 進度 UI。
async function startBreezeTranscribe() {
  $("#transcribe-title").textContent = "Breeze 轉字幕中…";
  $("#transcribe-msg").innerHTML =
    "本地 Breeze 轉錄各軌 → 自動標講者 → 匯入 → 校對，一次跑完。<br>" +
    '<em style="color:#888;font-size:12px">轉錄那段最久，請保留分頁、不要關閉。</em>';
  const adv = $("#transcribe-advanced");
  if (adv) adv.hidden = true;
  $("#transcribe-progress").hidden = false;
  // Breeze 階段跟 STT 三顆 pill 對不上 → 隱藏 pills，只用進度條 + 文字標籤
  const pills = document.querySelector("#transcribe-progress .phase-pills");
  if (pills) pills.hidden = true;
  $("#transcribe-fill").style.width = "0%";
  $("#transcribe-percent").textContent = "0%";
  $("#transcribe-phase-label").textContent = "啟動 Breeze…";
  _breezeStartMs = Date.now(); // 經過時間計時器起點
  const go = $("#transcribe-go");
  const cancel = $("#transcribe-cancel");
  if (go) go.disabled = true;
  // 轉錄中可取消：按下後禁用按鈕、通知後端砍 Breeze 子行程，收尾後回到可重轉狀態
  if (cancel) {
    cancel.disabled = false;
    cancel.textContent = "取消轉錄";
    cancel.onclick = abortTranscribeFromModal;
  }
  try {
    const r = await fetch("/api/transcribe/breeze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!r.ok && r.status !== 202) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
  } catch (e) {
    finishTranscribe({ ok: false, error: e.message });
    return;
  }
  stopTranscribePoll();
  _transcribePollTimer = setInterval(pollTranscribe, 500);
}

async function runTranscribe(file) {
  $("#transcribe-title").textContent = "轉字幕中…";
  $("#transcribe-msg").innerHTML =
    `處理中：<code>${file.path}</code><br>` +
    `<em style="color:#888;font-size:12px">請保留這個分頁，不要關閉。</em>`;
  $("#transcribe-advanced").hidden = true;
  $("#transcribe-progress").hidden = false;
  const _pillsT = document.querySelector("#transcribe-progress .phase-pills");
  if (_pillsT) _pillsT.hidden = false; // Breeze 會藏 pills，單軌轉錄要還原
  $("#transcribe-fill").style.width = "0%";
  $("#transcribe-percent").textContent = "0%";
  $("#transcribe-phase-label").textContent = "啟動中…";
  renderTranscribePhasePills(null, "running");

  const go = $("#transcribe-go");
  const cancel = $("#transcribe-cancel");
  go.disabled = true;
  // 轉錄中可取消：按下後禁用按鈕、通知後端砍 job，收尾後回到可重轉狀態
  cancel.disabled = false;
  cancel.textContent = "取消轉錄";
  cancel.onclick = abortTranscribeFromModal;

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
    // 重整後恢復的 job 也要能取消
    if (cancel) {
      cancel.disabled = false;
      cancel.textContent = "取消轉錄";
      cancel.onclick = abortPerMicFromModal;
    }
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
  // Breeze 模式：STT pills 對不上 Breeze 階段 → 隱藏，只用進度條 + 文字
  const _pills = document.querySelector("#transcribe-progress .phase-pills");
  if (_pills) _pills.hidden = s.mode === "breeze";
  if (s.mode === "breeze")
    $("#transcribe-title").textContent = "Breeze 轉字幕中…";
  const goBtn = $("#transcribe-go");
  const cancelBtn = $("#transcribe-cancel");
  if (goBtn) goBtn.disabled = true;
  // 重整後恢復的 job 也要能取消
  if (cancelBtn) {
    cancelBtn.disabled = false;
    cancelBtn.textContent = "取消轉錄";
    cancelBtn.onclick = abortTranscribeFromModal;
  }
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
    const isBreeze = s.mode === "breeze";
    const overall = isBreeze
      ? computeBreezeOverallPercent(phase, pct)
      : computeOverallPercent(phase, pct);
    $("#transcribe-fill").style.width = `${overall.toFixed(1)}%`;
    $("#transcribe-percent").textContent = `${overall.toFixed(0)}%`;
    let label = TRANSCRIBE_PHASE_LABELS[phase] || phase;
    if (isBreeze && _breezeStartMs) {
      // 經過時間計時器：即使模型載入期 percent 還是 0，也讓使用者看到有在動。
      label += `　已 ${fmtElapsed((Date.now() - _breezeStartMs) / 1000)}`;
    }
    $("#transcribe-phase-label").textContent = label;
    if (!isBreeze) renderTranscribePhasePills(phase, "running");
    return;
  }

  if (s.state === "done") {
    stopTranscribePoll();
    finishTranscribe({ ok: true, out_srt: s.out_srt });
    return;
  }

  if (s.state === "cancelled") {
    // job 被取消（例如另一個分頁按的；本頁的取消流程會先停 poll，不會走到這）
    stopTranscribePoll();
    resetTranscribeModalAfterCancel();
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
const MIC_SETUP_SPEAKERS = ["a", "b", "c", "d"];

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
    // 角色：feature 裡有這軌 = 來賓，否則主持（沿用已存的 camera_rule 回填）
    const feature = (state.cameraRule && state.cameraRule.feature) || {};
    const role = feature[sp] ? "guest" : "host";
    row.innerHTML = `
      <span class="mic-setup-row-key">軌 ${sp}</span>
      <select class="mic-setup-row-select" data-speaker="${sp}">${options.join("")}</select>
      <select class="mic-setup-row-role" data-speaker="${sp}" title="主持一律全景（cam A）；來賓連續講滿門檻秒數才切特寫（cam B）">
        <option value="host"${role === "host" ? " selected" : ""}>主持</option>
        <option value="guest"${role === "guest" ? " selected" : ""}>來賓</option>
      </select>
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

// 收角色：只收「有指派音檔」的軌（沒設檔的軌角色沒意義）。host/guest → 後端生成 camera_rule。
function collectMicSetupRoles() {
  const mics = collectMicSetupAssignment();
  const roles = {};
  document.querySelectorAll(".mic-setup-row-role").forEach((sel) => {
    const sp = sel.dataset.speaker;
    if (mics[sp]) roles[sp] = sel.value === "guest" ? "guest" : "host";
  });
  return roles;
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
  // min_sec 回填已存的 camera_rule（沒設預設 15）
  const minSecInput = $("#mic-setup-min-sec");
  if (minSecInput) {
    const m = Number((state.cameraRule || {}).min_sec);
    minSecInput.value = String(m > 0 ? m : 15);
  }

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
  const roles = collectMicSetupRoles();
  const minSecRaw = Number(($("#mic-setup-min-sec") || {}).value);
  const minSec = Number.isFinite(minSecRaw) && minSecRaw > 0 ? minSecRaw : 15;
  const go = $("#mic-setup-go");
  const cancel = $("#mic-setup-cancel");
  go.disabled = true;
  cancel.disabled = true;
  go.textContent = "儲存中…";

  try {
    const r = await fetch("/api/episode/mics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mics, roles, min_sec: minSec }),
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
  // 轉錄中可取消：按下後禁用按鈕、通知後端廢掉 job，收尾後回到可重轉狀態
  cancel.disabled = false;
  cancel.textContent = "取消轉錄";
  cancel.onclick = abortPerMicFromModal;

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

  if (s.state === "cancelled") {
    // job 被取消（例如另一個分頁按的；本頁的取消流程會先停 poll，不會走到這）
    stopPerMicPoll();
    resetPerMicModalAfterCancel();
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

// 進度 pill 是否代表「有 job 在跑」（啟動中 / 合成中）。用來取代脆弱的按鈕文字比對，
// 判斷取消鈕當下該打 /cancel（取消）還是純關閉。
function isAssembleActive() {
  const pill = $("#assemble-pill");
  const st = pill && pill.getAttribute("data-state");
  return st === "starting" || st === "running";
}

// 輪詢 assemble status 直到離開 running/preparing（或 timeout）。回傳最終 state。
// 後端 cancel 已同步等到 idle 才回；這是防後端 timeout 沒收乾淨的保險。
async function pollUntilAssembleIdle(timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  let last = "idle";
  while (Date.now() < deadline) {
    try {
      const r = await fetch("/api/assemble/status");
      const s = await r.json();
      last = s.state;
      if (s.state !== "running" && s.state !== "preparing") return s.state;
    } catch (e) {
      /* 暫時失敗，續試 */
    }
    await new Promise((res) => setTimeout(res, 200));
  }
  return last;
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
    const body = { targets, force, subtitle_mode: state.subtitleMode };
    if (previewSec) body.preview_sec = previewSec;
    if (state.subtitleMode === "overlay") {
      const ovSel = document.querySelector("#overlay-srt-select");
      const ovShift = document.querySelector("#overlay-shift-ms");
      const ovKeep = document.querySelector("#overlay-keep-all");
      body.overlay_srt = ovSel ? ovSel.value : "";
      body.overlay_shift_ms = ovShift
        ? Math.round(Number(ovShift.value) || 0)
        : 0;
      body.overlay_keep_all = ovKeep ? !!ovKeep.checked : false;
      if (!body.overlay_srt) {
        throw new Error("抽換字幕：請先在「字幕」旁選一份字幕檔");
      }
    }
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
    // prepare 期間被取消（後端在 ffmpeg 起來前收到 cancel）：不開 poll，取消流程會關 modal。
    const data = await r.json().catch(() => ({}));
    if (data.cancelled) {
      return;
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

  if (s.state === "preparing") {
    // 佔位過渡態：素材檢查中（ffprobe/建 srt），ffmpeg 還沒起來
    setAssemblePill("starting", "準備中");
    $("#assemble-current-label").textContent = "準備素材中…";
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
  let _pendingAssemble = null;

  // 「下一步」：點合成 → 先開「合成設定」視窗（設倍速等輸出選項）→ 按「開始合成」才真的跑
  const launch = (targets, title, { previewSec = null } = {}) => {
    $("#output-menu-btn")?._popover?.close(); // 合成設定視窗要蓋過下拉，先收掉
    _pendingAssemble = { targets, title, previewSec };
    syncOutputControls(); // 把目前 state.speed 反映到設定視窗的控制項
    showModal("assemble-setup-modal");
  };

  $("#assemble-yt-btn").addEventListener("click", () => {
    launch(["yt"], "合成 YT 16:9 完整版");
  });
  $("#assemble-reels-btn").addEventListener("click", () => {
    launch(["reels"], "合成 Reels 9:16 短版");
  });
  $("#assemble-mp3-btn")?.addEventListener("click", () => {
    launch(["mp3"], "輸出原速 MP3（純音訊）");
  });
  $("#assemble-preview-btn").addEventListener("click", () => {
    launch(["yt"], "合成 YT 前 5 分鐘預覽", { previewSec: 300 });
  });

  $("#assemble-setup-cancel").addEventListener("click", () => {
    _pendingAssemble = null;
    hideModal("assemble-setup-modal");
  });

  $("#assemble-setup-start").addEventListener("click", async () => {
    if (!_pendingAssemble) return;
    const { targets, title, previewSec } = _pendingAssemble;
    const startBtn = $("#assemble-setup-start");
    // 倍速等輸出設定要先寫進 episode.yaml，後端 assemble 才讀得到。
    // 沿用主儲存通道（postSave）：一併存下目前所有修改，與舊「先存再合成」一致。
    startBtn.disabled = true;
    setBtnLabel(startBtn, null, "儲存中…");
    try {
      await postSave(buildSavePayload({ withSpeed: true }));
      clearUndoStacks();
      await loadEpisodeState();
      renderCards();
    } catch (e) {
      alert(`儲存失敗，未開始合成：${e.message}`);
      startBtn.disabled = false;
      setBtnLabel(startBtn, null, "開始合成");
      return;
    }
    startBtn.disabled = false;
    setBtnLabel(startBtn, null, "開始合成");
    hideModal("assemble-setup-modal");
    _pendingAssemble = null;

    _lastAssembleTitle = title;
    resetAssembleModal();
    $("#assemble-title").textContent = title;
    showModal("assemble-modal");
    // 預覽模式預設 force=true，方便反覆驗證 bitrate / 畫質不被「輸出已存在」擋下
    startAssemble(targets, { previewSec, force: previewSec ? true : false });
  });

  $("#assemble-cancel").addEventListener("click", async () => {
    const btn = $("#assemble-cancel");
    // 已結束（done/error）→ 純關閉，不打 cancel。
    if (!isAssembleActive()) {
      stopAssemblePoll();
      hideModal("assemble-modal");
      return;
    }
    // job 還在跑：通知後端砍 ffmpeg，並等後端確認收回 idle 才關 modal。
    // 期間 modal 維持開啟＋按鈕 disable，擋掉「取消後空窗重按合成」撞 409。
    btn.disabled = true;
    btn.textContent = "取消中…";
    stopAssemblePoll(); // 停進度 poll，避免和取消狀態打架
    setAssemblePill("starting", "取消中…");
    $("#assemble-current-label").textContent = "取消中…";
    let finalState = "idle";
    try {
      const r = await fetch("/api/assemble/cancel", { method: "POST" });
      const d = await r.json().catch(() => ({}));
      finalState = d.state || "idle";
    } catch (e) {
      /* 取消請求失敗：改用輪詢確認後端狀態 */
      finalState = "running";
    }
    // 後端沒在回應內收乾淨（極少見，例如 join timeout）→ 再輪詢一小段
    if (finalState === "running" || finalState === "preparing") {
      finalState = await pollUntilAssembleIdle();
    }
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

// 鍵盤快捷鍵總覽：topbar 的 ? 按鈕與快捷鍵 ? 共用同一個 modal
$("#shortcuts-btn").addEventListener("click", () =>
  showModal("shortcuts-modal"),
);
$("#shortcuts-close").addEventListener("click", () =>
  hideModal("shortcuts-modal"),
);

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

  // 依講者推 A/B：雙機集（有 cam B）就顯示，跟 A/B 膠囊同一條件，讓入口好找。
  // 要有逐卡講者（speakers.json）才能按——分軌轉錄或 Breeze 轉錄都會產生，不限有 mics。
  const suggestRow = $("#cam-suggest-row");
  if (suggestRow) {
    const hasCamB = !!(state.cameras && state.cameras.b);
    const hasSpeakers = state.speakersMapping && state.speakersMapping.size > 0;
    suggestRow.hidden = !hasCamB;
    const sgBtn = $("#cam-suggest-btn");
    const sgHint = $("#cam-suggest-hint");
    if (sgBtn) sgBtn.disabled = !hasSpeakers;
    if (sgHint)
      sgHint.textContent = hasSpeakers
        ? "會覆蓋現有 A/B 切換（先自動備份）"
        : "需先產生逐卡講者（分軌轉錄或 Breeze 轉錄）才能推鏡頭";
  }

  showModal("cam-modal");
}

$("#cam-btn").addEventListener("click", openCamModal);
$("#cam-cancel").addEventListener("click", () => hideModal("cam-modal"));

// 依分軌講者 + camera_rule 自動推 A/B 切換點（覆蓋 cameras.json，先備份）。
$("#cam-suggest-btn")?.addEventListener("click", async () => {
  const btn = $("#cam-suggest-btn");
  const hint = $("#cam-suggest-hint");
  btn.disabled = true;
  if (hint) hint.textContent = "推算中…";
  try {
    const r = await fetch("/api/cameras-suggest", { method: "POST" });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${r.status}`);
    }
    const d = await r.json();
    await loadEpisodeState();
    renderCards();
    renderTopbar();
    if (hint)
      hint.textContent = `已推出 ${d.count} 個 A/B 切換點（可手動微調例外）`;
  } catch (e) {
    if (hint) hint.textContent = "";
    alert(`推鏡頭失敗：${e.message}`);
  } finally {
    btn.disabled = false;
  }
});

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

// 抓 cam-modal 目前狀態組 /api/save payload；align-all auto-save 跟 cam-save 共用。
// 一律以主存檔的 buildSavePayload() 為基底、只覆蓋 cam 相關欄位 —— 先前這裡手寫第二套
// builder，漏送 merges / time_overrides / new_cards / reels_clips / rotate / cover_enabled /
// subtitle_style / silence_trim 等欄位；存檔成功後 loadEpisodeState() 重載，主編輯器尚未
// 按「儲存」的那些編輯就被靜默清掉（資料遺失級 bug）。禁止再手寫欄位清單，主存檔新增
// 欄位時這裡自動跟上（tests/test_cam_modal_save_payload.py 鎖住此約定）。
function _camModalSavePayload() {
  const camAPath = $("#cam-a-select").value || "";
  const camBPath = $("#cam-b-select").value || "";
  const audioPath = $("#audio-select").value || "";
  const srtPath = $("#srt-select").value || "";
  const offset = Number($("#cam-sync-offset-b").value || 0);
  const audioOffset = Number($("#audio-sync-offset").value || 0);
  return {
    ...buildSavePayload(),
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
    const payload = _camModalSavePayload();
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
  const offsetRaw = $("#cam-sync-offset-b").value;
  const offset = offsetRaw === "" ? 0 : Number(offsetRaw);
  if (!Number.isFinite(offset)) {
    alert("同步偏移要是數字");
    return;
  }
  const audioOffsetRaw = $("#audio-sync-offset").value;
  const audioOffset = audioOffsetRaw === "" ? 0 : Number(audioOffsetRaw);
  if (!Number.isFinite(audioOffset)) {
    alert("音檔同步偏移要是數字");
    return;
  }
  const btn = $("#cam-save");
  btn.disabled = true;
  btn.textContent = "儲存中…";
  // 與 #align-all 共用同一個 payload builder（含 srt_path）：先前 cam-save 內聯自建 payload
  // 漏掉 srt_path → 在 cam-modal 切字幕檔按「儲存」存不進去、重開又跳回舊值。offset/audioOffset
  // 已於上方驗證為數字，builder 重讀同一組 DOM 值不會踩到它內部的靜默歸零。
  const payload = _camModalSavePayload();
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
// _leavingToDashboard：按「取消」已自行 confirm 過放棄變動，導去 dashboard 時不要再被瀏覽器攔第二次
let _leavingToDashboard = false;
window.addEventListener("beforeunload", (e) => {
  if (_leavingToDashboard || !hasUnsavedChanges()) return;
  e.preventDefault();
  e.returnValue = "";
});

$("#cancel-btn").addEventListener("click", async () => {
  if (hasUnsavedChanges() && !confirm("未儲存的修改會丟失，確定取消？")) return;
  // 取消 = 放棄本次編輯、回 dashboard 重選集（不再關掉 server；關 server 才是收工）
  try {
    const r = await fetch("/api/episodes/close", { method: "POST" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  } catch (_) {
    alert("回 dashboard 失敗");
    return;
  }
  _leavingToDashboard = true;
  window.location.href = "/";
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

// === 換集下拉：列出最近 / 掃到的集，一鍵 hot-swap（不離開頁面、不跳 osascript 對話框）===
let _epMenuOpen = false;

function closeEpSwitchMenu() {
  const menu = $("#ep-switch-menu");
  if (menu) menu.hidden = true;
  $("#ep-switch-btn")?.setAttribute("aria-expanded", "false");
  _epMenuOpen = false;
}

async function openEpSwitchMenu() {
  const menu = $("#ep-switch-menu");
  if (!menu) return;
  menu.hidden = false;
  $("#ep-switch-btn")?.setAttribute("aria-expanded", "true");
  _epMenuOpen = true;
  menu.innerHTML = "";
  const loading = document.createElement("div");
  loading.className = "ep-menu-msg";
  loading.textContent = "載入中…";
  menu.appendChild(loading);

  let eps = [];
  try {
    const r = await fetch("/api/episodes");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    eps = Array.isArray(data.episodes) ? data.episodes : [];
  } catch (e) {
    menu.innerHTML = "";
    const err = document.createElement("div");
    err.className = "ep-menu-msg ep-menu-error";
    err.textContent = `讀取集數失敗：${e.message}`;
    menu.appendChild(err);
    return;
  }
  if (!_epMenuOpen) return; // 載入期間使用者已關閉

  // 依 path 去重
  const seen = new Set();
  const items = eps.filter((e) => {
    const p = e && e.path;
    if (!p || seen.has(p)) return false;
    seen.add(p);
    return true;
  });

  menu.innerHTML = "";
  if (items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "ep-menu-msg";
    empty.textContent = "（沒有掃到其他集）";
    menu.appendChild(empty);
  }
  for (const e of items) {
    const isCurrent = e.name === state.name;
    const folder = (e.path || "").split("/").pop() || "";
    const row = document.createElement("button");
    row.type = "button";
    row.className = "ep-menu-item" + (isCurrent ? " is-current" : "");
    const nameEl = document.createElement("span");
    nameEl.className = "ep-menu-name";
    nameEl.textContent = e.name || folder;
    const folderEl = document.createElement("span");
    folderEl.className = "ep-menu-folder";
    folderEl.textContent = folder;
    row.append(nameEl, folderEl);
    if (isCurrent) {
      const badge = document.createElement("span");
      badge.className = "ep-menu-badge";
      badge.textContent = "目前";
      row.append(badge);
      row.disabled = true;
    } else {
      row.addEventListener("click", () => {
        closeEpSwitchMenu();
        if (
          hasUnsavedChanges() &&
          !confirm("有未儲存的修改，換集後會丟失，繼續？")
        )
          return;
        switchEpisode(e.path);
      });
    }
    menu.appendChild(row);
  }
  // 底部：選其他資料夾（osascript fallback，給不在清單上的集）
  const sep = document.createElement("div");
  sep.className = "ep-menu-sep";
  menu.appendChild(sep);
  const pick = document.createElement("button");
  pick.type = "button";
  pick.className = "ep-menu-item ep-menu-pick";
  pick.textContent = "選其他資料夾…";
  pick.addEventListener("click", () => {
    closeEpSwitchMenu();
    pickEpisodeFolder();
  });
  menu.appendChild(pick);
}

$("#ep-switch-btn")?.addEventListener("click", (e) => {
  e.stopPropagation();
  if (_epMenuOpen) closeEpSwitchMenu();
  else openEpSwitchMenu();
});
document.addEventListener("click", (e) => {
  if (_epMenuOpen && !e.target.closest(".ep-switch-wrap")) closeEpSwitchMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && _epMenuOpen) closeEpSwitchMenu();
});
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

$("#ep-new-btn")?.addEventListener("click", openNewEpModal);

// 插入一張新字卡：預設接在「上一張卡之後、且不與前後卡重疊」→ 出現空白卡、自動捲到並 focus 文字
$("#card-insert-btn").addEventListener("click", () => {
  if (state.needsTranscribe) return;
  const v = $("#video");
  const dur = v.duration || 0;
  const r2 = (x) => Math.round(x * 100) / 100;
  const t = r2(Math.max(0, v.currentTime || 0));
  // 依 start 排序的所有卡（含新卡 / 子卡），找游標所在/之前那張 prev：
  //   起點接在 prev.end（不重疊上一張），但不早於目前播放位置；
  //   終點預設 +1.5s，夾在下一張卡 start 與影片總長內（不重疊下一張）。
  const ranges = expandedCards()
    .map((e) => ({ start: e.start, end: e.end }))
    .sort((a, b) => a.start - b.start);
  let prev = null;
  for (const rg of ranges) {
    if (rg.start <= t + 1e-6) prev = rg;
    else break;
  }
  const start = r2(prev ? Math.max(t, prev.end) : t);
  let end = start + 1.5;
  const nextAfter = ranges.find((rg) => rg.start > start + 1e-6);
  if (nextAfter) end = Math.min(end, nextAfter.start);
  if (dur) end = Math.min(end, dur);
  end = r2(end);
  if (end < start + 0.1) end = r2(start + 0.1); // 沒空間時給最短 0.1s（容後手動讓位）
  pushUndo();
  const tempId = state.newCardSeq++;
  state.newCards.push({ tempId, start, end, text: "" });
  state.timeEditKey = null;
  renderCards();
  renderTopbar();
  renderCaption();
  requestAnimationFrame(() => {
    const el = document.querySelector(
      `#cards-list .card[data-idx="new:${tempId}"]`,
    );
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      const txt = el.querySelector(".card-text");
      if (txt) txt.focus();
    }
  });
});

// 拖拉換位置：grip 啟動拖曳，#cards-list 委派 dragover/drop（只在整卡之間，排除 sub-card / 新卡）
(() => {
  const list = $("#cards-list");
  if (!list) return;
  const clear = () =>
    document
      .querySelectorAll(".card.drop-before, .card.drop-after, .card.dragging")
      .forEach((el) =>
        el.classList.remove("drop-before", "drop-after", "dragging"),
      );
  const targetCard = (e) => {
    const card = e.target.closest && e.target.closest(".card");
    if (!card) return null;
    if (
      card.classList.contains("card-sub") ||
      card.classList.contains("card-new")
    ) {
      return null;
    }
    return card;
  };
  list.addEventListener("dragstart", (e) => {
    const grip = e.target.closest && e.target.closest(".card-grip");
    if (!grip) return;
    const card = grip.closest(".card");
    const idx = parseInt(card && card.dataset.idx, 10);
    if (Number.isNaN(idx)) return;
    state.dragCardIdx = idx;
    e.dataTransfer.effectAllowed = "move";
    try {
      e.dataTransfer.setData("text/plain", String(idx));
      e.dataTransfer.setDragImage(card, 12, 12); // 拖整張卡當 ghost，不是只有把手
    } catch (_) {}
    card.classList.add("dragging");
  });
  list.addEventListener("dragover", (e) => {
    if (state.dragCardIdx == null) return;
    const card = targetCard(e);
    if (!card) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const rect = card.getBoundingClientRect();
    const before = e.clientY < rect.top + rect.height / 2;
    document
      .querySelectorAll(".card.drop-before, .card.drop-after")
      .forEach((el) => {
        if (el !== card) el.classList.remove("drop-before", "drop-after");
      });
    card.classList.toggle("drop-before", before);
    card.classList.toggle("drop-after", !before);
  });
  list.addEventListener("drop", (e) => {
    if (state.dragCardIdx == null) return;
    const card = targetCard(e);
    if (!card) return;
    e.preventDefault();
    const targetIdx = parseInt(card.dataset.idx, 10);
    const before = card.classList.contains("drop-before");
    const dragIdx = state.dragCardIdx;
    state.dragCardIdx = null;
    clear();
    if (!Number.isNaN(targetIdx)) reorderCardTo(dragIdx, targetIdx, before);
  });
  list.addEventListener("dragend", () => {
    state.dragCardIdx = null;
    clear();
  });
})();
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

// === 輸出設定：旋轉拉正 / 節目封面 / 倍速 / 合成字幕模式 ===
// state → UI 控制項（換集載入、undo 後呼叫）
function syncOutputControls() {
  const cover = document.querySelector("#cover-toggle");
  if (cover) cover.checked = !!state.coverEnabled;
  const spd = document.querySelector("#speed-toggle");
  const spf = document.querySelector("#speed-factor");
  if (spd) spd.checked = !!(state.speed && state.speed.enabled);
  if (spf) {
    spf.value = String((state.speed && state.speed.factor) || 1.15);
    spf.disabled = !(state.speed && state.speed.enabled);
  }
  const sil = document.querySelector("#silence-toggle");
  const silMin = document.querySelector("#silence-min");
  if (sil) sil.checked = !!(state.silenceTrim && state.silenceTrim.enabled);
  if (silMin) {
    silMin.value = String(
      (state.silenceTrim && state.silenceTrim.minSilence) || 0.8,
    );
    silMin.disabled = !(state.silenceTrim && state.silenceTrim.enabled);
  }
  const sm = document.querySelector("#subtitle-mode-select");
  if (sm) sm.value = state.subtitleMode || "burn";
  syncOverlayControls();
  syncRotateControls();
}

// 抽換字幕：依字幕模式顯示/隱藏 overlay 控制項，並用集內 .srt 候選填字幕檔下拉
function syncOverlayControls() {
  const wrap = document.querySelector("#overlay-controls");
  if (!wrap) return;
  const on = state.subtitleMode === "overlay";
  wrap.hidden = !on;
  if (!on) return;
  const sel = document.querySelector("#overlay-srt-select");
  if (!sel) return;
  const cands = Array.isArray(state.srtCandidates) ? state.srtCandidates : [];
  const prev = sel.value;
  sel.innerHTML = "";
  if (!cands.length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "（集資料夾找不到 .srt）";
    sel.appendChild(o);
    return;
  }
  for (const c of cands) {
    const o = document.createElement("option");
    o.value = c;
    o.textContent = c;
    sel.appendChild(o);
  }
  // 保留先前選擇；否則優先挑名字含 修正/修改/caption 的，再退回第一個
  if (prev && cands.includes(prev)) {
    sel.value = prev;
  } else {
    const pref = cands.find((c) => /修正|修改|caption|correct/i.test(c));
    sel.value = pref || cands[0];
  }
}

function setupOutputControls() {
  const clampDeg = (v) => {
    v = Number(v);
    if (!isFinite(v)) v = 0;
    return Math.round(Math.max(-15, Math.min(15, v)) * 10) / 10;
  };
  // 旋轉：滑桿 + 數字雙向綁定，編目前 active cam（跟 crop 共用 A/B context）
  const slider = document.querySelector("#rotate-slider");
  const num = document.querySelector("#rotate-input");
  function setRotate(deg, push) {
    const cam = activeRotateCam();
    const v = clampDeg(deg);
    if ((Number(state.rotate[cam]) || 0) === v) {
      syncRotateControls();
      return;
    }
    if (push) pushUndo();
    state.rotate[cam] = v;
    state.outputDirty = true;
    renderCropInfo(); // 套 CSS 旋轉預覽 + 同步滑桿/數字/徽章
    renderTopbar();
  }
  let rotateDragPushed = false;
  if (slider) {
    slider.addEventListener("input", () => {
      setRotate(slider.value, !rotateDragPushed); // 拖一次只 push 一筆 undo
      rotateDragPushed = true;
    });
    slider.addEventListener("change", () => {
      rotateDragPushed = false;
    });
  }
  if (num) num.addEventListener("change", () => setRotate(num.value, true));
  const reset = document.querySelector("#rotate-reset");
  if (reset) reset.addEventListener("click", () => setRotate(0, true));

  // 節目封面開關
  const cover = document.querySelector("#cover-toggle");
  if (cover)
    cover.addEventListener("change", () => {
      state.coverEnabled = cover.checked;
      state.outputDirty = true;
      renderTopbar();
    });

  // 倍速開關 + 倍率
  const spd = document.querySelector("#speed-toggle");
  const spf = document.querySelector("#speed-factor");
  if (spd)
    spd.addEventListener("change", () => {
      state.speed.enabled = spd.checked;
      if (spf) spf.disabled = !spd.checked;
      state.outputDirty = true;
      renderTopbar();
    });
  if (spf)
    spf.addEventListener("change", () => {
      let v = Number(spf.value);
      if (!isFinite(v)) v = 1.15;
      v = Math.round(Math.max(0.5, Math.min(2, v)) * 100) / 100;
      state.speed.factor = v;
      spf.value = String(v);
      state.outputDirty = true;
      renderTopbar();
    });

  // 去空拍開關 + 最短停頓門檻
  const sil = document.querySelector("#silence-toggle");
  const silMin = document.querySelector("#silence-min");
  if (sil)
    sil.addEventListener("change", () => {
      state.silenceTrim.enabled = sil.checked;
      if (silMin) silMin.disabled = !sil.checked;
      state.outputDirty = true;
      renderTopbar();
    });
  if (silMin)
    silMin.addEventListener("change", () => {
      let v = Number(silMin.value);
      if (!isFinite(v)) v = 0.8;
      v = Math.round(Math.max(0.3, Math.min(5, v)) * 10) / 10;
      state.silenceTrim.minSilence = v;
      silMin.value = String(v);
      state.outputDirty = true;
      renderTopbar();
    });

  // 合成字幕模式（per-assemble，不寫 yaml）
  const sm = document.querySelector("#subtitle-mode-select");
  if (sm)
    sm.addEventListener("change", () => {
      state.subtitleMode = ["sidecar", "overlay"].includes(sm.value)
        ? sm.value
        : "burn";
      syncOverlayControls();
    });

  syncOutputControls();
}

// 通用下拉浮層：點觸發鈕開合，點外面或按 Esc 關閉。close/open 掛在 btn._popover 供他處呼叫。
function setupPopover(btnId, menuId) {
  const btn = $("#" + btnId);
  const menu = $("#" + menuId);
  if (!btn || !menu) return null;
  const close = () => {
    if (menu.hidden) return;
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    btn.classList.remove("popover-open");
  };
  const open = () => {
    menu.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    btn.classList.add("popover-open");
  };
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (menu.hidden) open();
    else close();
  });
  // 選單內部互動（改下拉、打字、按合成）不該關閉浮層
  menu.addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", close);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
  });
  btn._popover = { open, close };
  return btn._popover;
}

setupVersionTabs();
setupCaptionSize();
$("#transcribe-breeze-btn")?.addEventListener("click", startBreezeTranscribe);
setupReelsClips();
setupAssembleButtons();
setupOutputControls();
setupSusToolbar();
setupSrtShift();
setupPopover("output-menu-btn", "output-menu");
setupPopover("srt-shift-toggle", "srt-shift-menu");

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
