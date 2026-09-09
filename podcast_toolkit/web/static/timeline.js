// 字幕時間軸模組（Phase 3 從 app.js 抽出的第一刀）。
// 負責 #card-timeline 一整塊：block 幾何 / 波形 / 靜音導引 / 縮放 / 拖曳改進出點 / 播放頭。
//
// 這裡跟 app.js 是**循環 import**（app.js 匯入本檔的 8 個出口，本檔匯入 app.js 的 8 個共用符號）。
// 循環在 ESM 合法，但有一條規則不能破：
//   **本檔的模組求值階段（top-level）絕對不可以「呼叫」或「讀取」任何從 app.js 匯入的東西。**
// 因為 app.js 是進入點，本檔會先被求值，那時 app.js 的 const/let 還在 TDZ，碰到就當場拋錯。
// 目前所有匯入符號都只在函式體內用 —— 新增 top-level 程式碼前先確認這條仍成立。
// （`_lastActiveKey` 是 app.js 的 let，這裡只讀不寫；live binding 唯讀，要改請改 app.js 那端。）

import {
  $,
  state,
  expandedCards,
  fmtTime,
  fmtTimeCard,
  pushUndo,
  rerenderEditState,
  _lastActiveKey,
} from "./app.js";

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
  // 起訖也寫在 DOM 上：block 的 click（跳到這一句）跟 title 都要讀「當下」的時間。
  // 只留 render 當時的 closure 值會過期 —— 用 ⏱ 工具列改完時間後（不重建時間軸），
  // 點下去會跳到改之前的位置。
  el.dataset.start = String(start);
  el.dataset.end = String(end);
}

// 只更新單一 block 的幾何與已改標記。⏱ 工具列每按一次微調鍵就要同步一次，
// 走 renderCardTimeline() 會整條重建（本集 1400+ 塊）太重，這裡只動受影響那一塊。
function syncTimelineBlock(key, start, end, edited) {
  const tl = $("#card-timeline");
  if (!tl || !tl.dataset.total) return;
  const t0 = Number(tl.dataset.t0);
  const total = Number(tl.dataset.total);
  const block = tl.querySelector(`.tl-block[data-key="${String(key)}"]`);
  if (block) {
    _tlSetBlockGeom(block, start, end, t0, total);
    block.classList.toggle("edited", !!edited);
    const txt = block.title.split("\n").slice(1).join("\n");
    block.title = `${fmtTime(start)} – ${fmtTime(end)}（${(end - start).toFixed(1)}s）\n${txt}`;
    return;
  }
  // 切過的卡在時間軸上是好幾個子塊，子塊時間由封套重算 → 只能整條重建。
  // 沒切的新增卡兩邊都找不到，什麼都不做（它本來就沒有 block）。
  if (tl.querySelector(`.tl-block[data-key^="${String(key)}:"]`))
    renderCardTimeline();
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
      // 讀 dataset 不讀 r.start：時間改過之後這個 closure 就過期了
      $("#video").currentTime = Number(block.dataset.start);
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

  drawSilenceGuides(ctx, wf, t0, total, backW, backH);
}

// 靜音區間的視覺參考線：只給眼睛對齊，不做任何吸附（吸附已於 2026-08 移除，見 onTimelineDragMove）。
// 畫成一條淡帶＋兩側細邊，帶子本身就是靜音範圍、邊界就是可以下刀的位置。
const TL_GUIDE_MIN_ZOOM = 2; // 1×（總覽）時邊界密到變雜訊，放大後才顯示
const TL_GUIDE_MIN_GAP_PX = 5; // 兩條邊界靠太近就只畫前一條，避免糊成一片
function drawSilenceGuides(ctx, wf, t0, total, backW, backH) {
  const sil = wf && wf.silences;
  if (!Array.isArray(sil) || !sil.length) return;
  if (state.tlZoom < TL_GUIDE_MIN_ZOOM) return;

  const dpr = window.devicePixelRatio || 1;
  const minGap = TL_GUIDE_MIN_GAP_PX * dpr;
  const x = (t) => ((t - t0) / total) * backW;
  const edgeW = Math.max(1, Math.round(dpr));

  ctx.save();
  let lastEdge = -Infinity;
  for (const s of sil) {
    if (!Array.isArray(s) || s.length < 2) continue;
    const x0 = x(s[0]);
    const x1 = x(s[1]);
    if (x1 < 0 || x0 > backW) continue; // 不在可視範圍
    if (x0 - lastEdge < minGap && x1 - lastEdge < minGap) continue;

    ctx.fillStyle = "#9b9ba8"; // --text-dim，刻意不用 accent（那是波形的顏色）
    ctx.globalAlpha = 0.08;
    ctx.fillRect(x0, 0, Math.max(1, x1 - x0), backH);
    ctx.globalAlpha = 0.35;
    ctx.fillRect(x0, 0, edgeW, backH);
    ctx.fillRect(x1 - edgeW, 0, edgeW, backH);
    lastEdge = x1;
  }
  ctx.restore();
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
  // 游標位置直接就是時間，不做任何吸附。
  // 曾有「吸附靜音邊界」邏輯，2026-08 移除：半徑寫成 Math.min(0.3, (8/rect.width)*total)，
  // 但 TL_ZOOM_MAX=60 讓像素換算項恆大於 0.3 → 實際半徑永遠是 0.3s 的固定死區，
  // 越放大想精修越被吸走。靜音邊界改畫成參考線（drawTlWaveform），只給眼睛看、不搶手。
  let t = t0 + ((e.clientX - rect.left) / rect.width) * total;
  let syncKey = null;
  let syncStart = 0;
  let syncEnd = 0;
  // 新增字卡沒有來源卡（c 為 null），時間存在 state.newCards 上，不能寫進 cardTimings ——
  // "new:0" 這種 key 送到後端 _parse_composite_id 會 int("new") 炸掉整次存檔。
  // 同理它與任何卡都不算「同源觸接」（沒有 idx 可比），touch 一律 false。
  const writeTime = (s, en) => {
    if (cur.newCard) {
      cur.newCard.start = Math.round(s * 100) / 100;
      cur.newCard.end = Math.round(en * 100) / 100;
    } else {
      state.cardTimings.set(cur.key, { start: s, end: en });
      block.classList.add("edited");
    }
  };
  if (edge === "start") {
    // 同源觸接子卡：拖 start 同步把前一段 end 拉到同位，維持連續不留空窗
    const touch =
      prev &&
      prev.c &&
      cur.c &&
      prev.c.idx === cur.c.idx &&
      Math.abs(prev.end - cur.start) < 0.06;
    const lo = prev ? (touch ? prev.start + TL_MIN_DUR : prev.end) : t0;
    t = Math.max(lo, Math.min(cur.end - TL_MIN_DUR, t));
    writeTime(t, cur.end);
    _tlSetBlockGeom(block, t, cur.end, t0, total);
    if (touch) {
      syncKey = prev.key;
      syncStart = prev.start;
      syncEnd = t;
    }
  } else {
    const touch =
      next &&
      next.c &&
      cur.c &&
      next.c.idx === cur.c.idx &&
      Math.abs(next.start - cur.end) < 0.06;
    const hi = next ? (touch ? next.end - TL_MIN_DUR : next.start) : t0 + total;
    t = Math.max(cur.start + TL_MIN_DUR, Math.min(hi, t));
    writeTime(cur.start, t);
    _tlSetBlockGeom(block, cur.start, t, t0, total);
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
  // 拖曳讀值：跟游標顯示這一刻的精確時間（0.01s，與存檔精度一致）
  if (_tlDrag.readout) {
    _tlDrag.readout.textContent = fmtTimeCard(t);
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

export {
  TL_ZOOM_MIN,
  TL_ZOOM_MAX,
  TL_ZOOM_STEP,
  drawTlWaveform,
  renderCardTimeline,
  setTlZoom,
  syncTimelineBlock,
  updateTimelinePlayhead,
};
