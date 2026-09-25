// 主編輯器的「畫面怎麼長出來」層：頂欄、字幕預覽、鏡頭／講者尺規、
// 空拍與待複查工具列、版本標籤。
//
// 邊界（D6 第一刀，2026-09-25 抽出，見 docs/plans/2026-09-25-app-js-split-render.md）：
//   該進來：只讀 state／DOM、把結果寫進畫面的純渲染函式，以及只被渲染用到的換算工具。
//   不該進來：改 state 的動作、事件綁定、後端往返。那些留在 app.js。
//   還沒進來（留給後續刀次）：renderCards、buildTimeToolbar、renderNewCardRow、
//   renderTypo、renderCropInfo、renderTrimControls、renderCaptionStyleControls
//   ——它們對 app.js 內部狀態的依賴面還太寬，先有這個落點再逐步挪。
//
// 循環 import 的鐵律（與 timeline.js／api.js 同一形狀）：
//   app.js 是進入點，本檔求值時它的 const/let 還在 TDZ。
//   **本檔模組求值階段（top-level）絕不可呼叫或讀取任何從 app.js 匯入的東西**，
//   只能在函式體內（被呼叫時）讀。違反的症狀是開頁即 ReferenceError，整個編輯器空白。
import {
  $,
  checkedDeletionSeconds,
  expandedCards,
  fmtTime,
  getActiveCrop,
  state,
  unsavedCount,
} from "./app.js";

// 字幕樣式 ASS → CSS 的單一換算來源（與影片模式共用）
import { buildSubtitleCss } from "./timeline-core.js";

export function renderTopbar() {
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
  // B1：對不上任何一張卡的時間版刪段（多半是在影片模式裡句中／跨停頓剪的）。
  // 本編輯器改不動它們，但它們出片時照剪 —— 不顯示就等於「看不到卻會生效」。
  const foreign = (state.foreignCuts || []).length;
  if (foreign > 0) {
    const secs = (state.foreignCuts || []).reduce((a, [s0, e0]) => a + (e0 - s0), 0);
    line += ` · 影片模式剪段 ${foreign} 段（${secs.toFixed(1)}s，本頁不可編輯）`;
  }
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

// 目前分頁（YT / Reels）該套哪份字幕樣式。Reels 缺欄位時回退 YT，與後端同規則。
export function captionPreviewStyle() {
  return state.activeVersion === "reels"
    ? state.subtitleStyleReels || state.subtitleStyleYt
    : state.subtitleStyleYt;
}

// 算 caption preview 的縮放比：對齊 ffmpeg ASS 輸出（預覽渲染高 / 輸出高）。
// 樣式數值（字級、描邊、陰影、margin_v）全是相對輸出解析度的 px，
// 乘上這個比例後，不管瀏覽器 zoom 多少、視窗縮多大，字幕跟畫面的比例都等同最終輸出。
function computeCaptionScale() {
  const wrap = document.querySelector(".video-wrap");
  if (!wrap) return null;
  const wrapHeight = wrap.clientHeight;
  if (!wrapHeight) return null;
  const res =
    state.activeVersion === "reels" ? state.outputResReels : state.outputResYt;
  // 缺資料就維持 app.css 的保底樣式（loadEpisodeState 完成前）
  if (!res) return null;
  const outH = Number(res.h);
  if (!outH) return null;
  const c = getActiveCrop();
  // 有 crop：用 crop 區的渲染高（= wrap高 × crop.height）
  // 無 crop：用整個 wrap 高（= 整個源 frame，YT 直接代表 1080 輸出）
  const baseHeight = c ? wrapHeight * c.height : wrapHeight;
  return baseHeight / outH;
}

// 套在 overlay 本體上的（可繼承或影響整塊）
const CAPTION_BOX_CSS_KEYS = [
  "fontFamily",
  "fontSize",
  "fontWeight",
  "color",
  "textShadow",
];

// 套在每一行上的：底色塊要貼著字，鋪滿整條 overlay 就不是成品的樣子了；
// color 也重覆套一次，否則 app.css 的 .caption-line.speaker-* 規則會壓過繼承值。
const CAPTION_LINE_CSS_KEYS = ["background", "padding", "borderRadius", "color"];

// 最近一次算出來的 CSS：renderCaption 每幀都跑，新建的行直接沿用這份，
// 不用每幀重算一次十二向描邊字串。
let _captionCss = null;

function applyCaptionLineStyle(line) {
  for (const k of CAPTION_LINE_CSS_KEYS) {
    line.style[k] = _captionCss ? _captionCss[k] : "";
  }
}

// 把 episode.yaml 的字幕樣式換算成預覽 CSS（與影片模式同一份 buildSubtitleCss）。
// 這裡以前只設 fontSize，顏色/描邊/粗體/底色塊寫死在 app.css → 預覽 ≠ 成品。
export function applyCaptionStyle() {
  const overlay = document.querySelector("#caption-overlay");
  if (!overlay) return;
  const scale = computeCaptionScale();
  const style = captionPreviewStyle();
  _captionCss = scale == null || !style ? null : buildSubtitleCss(style, scale);
  for (const k of CAPTION_BOX_CSS_KEYS) {
    overlay.style[k] = _captionCss ? _captionCss[k] : "";
  }
  overlay
    .querySelectorAll(".caption-line")
    .forEach((line) => applyCaptionLineStyle(line));
}

// === 字幕字級調整（crop 比例旁）：依目前分頁調 YT / Reels 各自的 subtitle_style.font_size ===
// 直接改 state 裡的 style 物件 + 即時預覽；存檔時 buildSavePayload 帶上、save_state 寫進 yaml。
export function activeSubtitleStyle() {
  return state.activeVersion === "reels"
    ? state.subtitleStyleReels
    : state.subtitleStyleYt;
}

export function renderCaptionSizeControl() {
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

// 把字幕塊放到裁切框（或整個影片框）內的垂直位置。
// 注意：libass 燒在最終 output frame 上，margin_v 是 output px；
// 預覽裁切框 = output frame，故偏移量 = margin_v/output_h × frameH。
// bucket 語意與影片模式共用（timeline-core.js subtitleAlignmentBucket）。
export function placeCaptionOverlay(overlay, bucket, frameY, frameH, marginFrac) {
  if (bucket === "top") {
    overlay.style.bottom = "";
    overlay.style.transform = "";
    overlay.style.top = `${((frameY + frameH * marginFrac) * 100).toFixed(2)}%`;
    return;
  }
  if (bucket === "middle") {
    overlay.style.bottom = "";
    overlay.style.top =
      `${((frameY + frameH * (0.5 + marginFrac)) * 100).toFixed(2)}%`;
    overlay.style.transform = "translateY(-50%)";
    return;
  }
  overlay.style.top = "";
  overlay.style.transform = "";
  overlay.style.bottom =
    `${((1 - frameY - frameH + frameH * marginFrac) * 100).toFixed(2)}%`;
}

// 回傳 expandedCards() 中包住 t 的那筆 — 切過的卡會在這裡命中對應 sub-card，
// 預覽/highlight/cam-B 切換都靠這個吃 composite key 與切後時間。
// tight-pack 模式下 sub-cards 尾段沒分到字幕（trailing silence），
// 若 t 落在原 cue 範圍內但沒命中 sub-card → 回傳該 cue 的最後一張 sub-card，
// 避免 highlight 在原 cue 中段突然消失，看起來「對不上 / 亂跳」。
export function activeCardAt(t) {
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

// 有講者標（分軌 mics 或 Breeze speakers.json）→ 預覽走雙行那條路（renderCaption 用）
function showsTwoLineCaption() {
  return (
    (state.mics && Object.keys(state.mics).length > 0) || !!state.hasSpeakerTags
  );
}

// 分軌版：拿出 t 當下所有 active 卡（可能不只一張：兩人同時講話 → 兩張不同 speaker 的卡同時在跑）。
// 單軌集 / 沒重疊 → 回 [activeCardAt] 退化結果，給 renderCaption 統一邏輯用。
// 只認「時間真的重疊」，不做延長上一句人為製造重疊的前處理——Breeze 相鄰卡 gap
// 幾乎恆為 0，人為延長會讓每次換講者都疊（分開講≠同時講，使用者裁決）。
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

// 算這張卡實際生效的 speaker：speakers sidecar 是每張卡都有明確值
// （由 srt_merge 從 N 路 mic SRT merge 出來），不需要 carry-forward。
// 沒值 = 單軌集或 sidecar 缺漏 → 回 null，UI 隱藏 speaker tag / ruler。
// 切過的卡：sub-card 都繼承原卡的 speaker（切句不會切換講者）。
export function computeEffectiveSpeaker(key) {
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
export function speakerLabel(sp) {
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
export function buildEffectiveCameraMap(rendered) {
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
export function computeEffectiveCamera(key) {
  return buildEffectiveCameraMap(expandedCards()).get(key) ?? "a";
}

// 整集 A/B 分布 ruler：依 expandedCards + carry-forward 染色，按時長比例算寬度
// 沒 cam B 時整條藏掉；hover 段落看時間範圍
export function renderCamRuler() {
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

// 分軌集講者分布 ruler：同 cam-ruler，但用 speakers sidecar 染色（每 speaker 一色）
// 跟 cam ruler 的差異：speaker 沒 carry-forward；沒掛 speaker 的段不畫（避免被誤解成「預設講者」）
export function renderSpeakerRuler() {
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

// 一行字幕的 DOM：每幀都可能重建，樣式直接沿用上次算好的那份（見 applyCaptionStyle）
function captionLineEl(text) {
  const line = document.createElement("div");
  line.className = "caption-line";
  line.textContent = text;
  applyCaptionLineStyle(line);
  return line;
}

export function renderCaption() {
  const overlay = $("#caption-overlay");
  const t = $("#video").currentTime;
  // 有講者標（分軌 mics 或 Breeze speakers.json）→ 找所有 active 卡分行
  //   （兩人同時講話 → 上下兩行 + speaker 著色；分講者切卡的集多半每刻單卡 = 單行帶著色）
  // 純單軌無講者 → 退回單張卡的純文字（舊行為）
  const showTwoLine = showsTwoLineCaption();
  if (!showTwoLine) {
    const r = activeCardAt(t);
    if (!r || state.deletions.has(r.key)) {
      overlay.textContent = "";
      overlay.classList.remove("multi-speaker");
      return;
    }
    // 單行也包成 .caption-line：border_style=3 的不透明底色塊要貼著字，
    // 套在整條 overlay 上會鋪成一條橫槓，跟成品不一樣。
    const only = captionLineEl(r.text);
    overlay.replaceChildren(only);
    overlay.classList.remove("multi-speaker");
    return;
  }
  const rows = activeCardsAt(t).filter((r) => !state.deletions.has(r.key));
  if (rows.length === 0) {
    overlay.textContent = "";
    overlay.classList.remove("multi-speaker");
    return;
  }
  // 陣列順序 = 畫面由上到下：先開始的排上面（新來的從下排進場、舊的往上讓），
  // 同時開始才比講者 key 當 tie-break。與後端 dual_line.layout 的排序鍵
  // (start, speaker, i) 一致，否則預覽與成品的上下兩排會相反。
  rows.sort((a, b) => {
    if (a.start !== b.start) return a.start - b.start;
    const sa = computeEffectiveSpeaker(a.key) || "";
    const sb = computeEffectiveSpeaker(b.key) || "";
    return sa.localeCompare(sb);
  });
  overlay.innerHTML = "";
  for (const r of rows) {
    const sp = computeEffectiveSpeaker(r.key);
    const line = captionLineEl(r.text);
    if (sp) line.classList.add(`speaker-${sp}`);
    overlay.appendChild(line);
  }
  overlay.classList.toggle("multi-speaker", rows.length > 1);
}

export function renderCardSkeletons(n = 8) {
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
export function reviewReasonLabel(r) {
  return { half_sentence: "半句結尾", repetition: "疑似重複幻覺" }[r] || r;
}

// 「待複查卡」= resegment 旗標（半句 / 幻覺）或空拍卡。導覽 / 篩選共用這個判斷。
export function cardNeedsReview(c) {
  return !!(c.needs_review || c.suspicious_pause);
}

// 鏡頭 A/B 鈕的 tooltip：真實狀態是「生效鏡頭 eff」×「有無 explicit 標記」四格。
// 舊版 A/B 各寫一組三元、判準是 mapping 存不存在而非它的值，於是 explicit 標 b
// 的卡上 A 鈕會寫成「目前鏡頭（已 explicit 標記）」——兩句都錯。
// 同一件事只在這裡產生一次，兩顆鈕共用。
export function camBtnTitle(which, eff, mapped) {
  const up = which.toUpperCase();
  if (eff === which) {
    const how = mapped === which ? "已 explicit 標記" : "沿用前一張";
    return `鏡頭 ${up}：目前鏡頭（${how}）`;
  }
  return `鏡頭 ${up}：切到 ${up} 鏡頭`;
}

// 紅卡 toolbar：總可疑數 / 已勾數 / 全選 / 刪除已勾
export function renderSusToolbar() {
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
  // 已勾數 + 移除秒數預覽（已含 cut_pad 延伸；標「約」是因為它算的是增量）
  const checkedEl = $("#sus-checked-count");
  if (checkedCount > 0) {
    const secs = checkedDeletionSeconds();
    checkedEl.textContent = `已勾 ${checkedCount}·約 ${secs.toFixed(1)} 秒`;
    checkedEl.title = "已含每側 cut_pad 延伸；與既有刪段相鄰時成品會再少一點";
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
export function renderReviewToolbar() {
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

// 純函式：解析 BUILD_ID → {version, sha, date, ts}，無法解析回 null。
// BUILD_ID 格式：`${VER}+g${SHA}.${BUILD_TS}`，例 `0.2.0+g3f9a1c2.20260717T1802`。
//   - version：以第一個 `+g` 切左段（version 可含 `.`，故用 indexOf 不用 split 全切）
//   - 右段用「最後一個 `.`」(lastIndexOf) 切：右邊是 ts、左邊是 sha（sha 不含 `.`，可能帶 -dirty）
//   - date：ts 命中 YYYYMMDDThhmm 才轉 YYYY-MM-DD，否則留空字串
// 供顯示邏輯與單元測試共用；"dev"、空值、格式不符一律回 null（顯示層自行決定文案）。
function parseBuildId(buildId) {
  if (typeof buildId !== "string" || !buildId) return null;
  if (buildId === "dev") return null;
  const gi = buildId.indexOf("+g");
  if (gi < 0) return null;
  const version = buildId.slice(0, gi);
  const rest = buildId.slice(gi + 2); // 去掉 "+g"
  const di = rest.lastIndexOf(".");
  const sha = di < 0 ? rest : rest.slice(0, di);
  const ts = di < 0 ? "" : rest.slice(di + 1);
  const m = ts.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})$/);
  const date = m ? `${m[1]}-${m[2]}-${m[3]}` : "";
  return { version, sha, date, ts };
}

// 依「執行中版本」memId 更新版本標籤（四態文案 + title=完整 build_id）。
// memId 傳 null／未知＝抓不到執行中版本 → 「版本 未知」。dev → 「開發版」。
export function renderVersionLabel(memId) {
  const el = document.getElementById("version-label");
  if (!el) return;
  if (memId === "dev") {
    el.textContent = "開發版";
    el.title = "dev";
    return;
  }
  const info = parseBuildId(memId);
  if (!info) {
    // error/empty 態：抓失敗、404、5xx、格式不符 → 未知
    el.textContent = "版本 未知";
    el.title = "/api/version 無回應";
    return;
  }
  // success 態：有日期顯示「v版本 · 日期」，時間戳解析失敗只顯示「v版本」
  el.textContent = info.date
    ? `v${info.version} · ${info.date}`
    : `v${info.version}`;
  el.title = memId; // 滑鼠停留看完整 build_id（含 sha），供診斷
}
