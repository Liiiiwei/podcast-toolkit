// 時間軸共用核心（T1，docs/plans/2026-09-23-unified-timeline-core.md）。
// podcast 模式（timeline.js）與影片模式（video-edit-prototype.js）過去把這五項邏輯
// 各自複製一份（影片側函式註解自己寫著「改寫自 app.js」）——本檔把它們併成單一
// source of truth，兩邊改成 import 使用。
//
// 硬邊界（不動）：
//   · 不碰刪除語意：podcast 的 deletions(Set<idx>) 與影片的 cuts([[start,end]]) 互斥，
//     本檔完全不涉及刪除/剪除狀態。
//   · 不碰持久化：本檔只做幾何/繪圖計算與 DOM 寫入，不呼叫任何 /api/*。
//
// T4（本次新增）：把標題卡軌的「排版分軌 + 靜態渲染 + 拖曳事件」三段
// （原本各自在 video-edit-prototype.js 的 assignCardLanes/renderCardTrack/bindCardTimeDrag）
// 併成 assignCardLanes/renderCardTrackCore/bindCardTimeDragCore 三個純函式匯出。
// D2：cut/partial 這類「卡是否被剪掉」的狀態純屬影片（依賴 cuts），
//     本檔不做判斷，一律透過呼叫端注入的 getStatus(c) callback 取得。
// D3：本檔完全不寫 state、不呼叫 /api/save，podcast 端是否把標題卡存檔
//     由呼叫端（timeline.js/app.js）自行決定，與本檔無關。
//
// 這是純函式模組：不 import app.js 或 video-edit-prototype.js 的任何符號，
// 所有輸入（DOM 元素、狀態值、callback）一律由呼叫端傳入，避免 ESM 循環依賴與 TDZ 風險。
// 座標換算全部參數化 t0：podcast 傳字幕窗起點（可能非 0），影片傳 0（整片）。

/** 時間 → 百分比（0–100，未夾制），t0/total 是呼叫端當下的時間軸窗口。 */
export function timeToPct(t, t0, total) {
  return ((t - t0) / total) * 100;
}

/** 時間 → 像素（相對某個寬度 width，未夾制）。 */
function timeToX(t, t0, total, width) {
  return ((t - t0) / total) * width;
}

/** 像素 → 時間（timeToX 的反函式），供波形逐欄取樣用。 */
function pxToTime(x, width, t0, total) {
  return t0 + (x / width) * total;
}

/**
 * 時長 → 百分比寬度（NOT 時間點 → 百分比）。
 * 易混淆處：t0=0（影片模式）時 timeToPct(d,0,total) 與 durToPct(d,total) 算出來的數字
 * 剛好相同，過去影片端的 pct() 因此能一魚兩吃（既算絕對位置也算寬度）。
 * podcast 端 t0 通常非 0（字幕窗起點），這時只有「絕對時間點」該減 t0，
 * 「時長轉寬度」不能減 —— 兩者必須是兩條公式，否則卡片寬度會整條偏移。
 */
function durToPct(d, total) {
  return (d / total) * 100;
}

function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

// === 播放頭定位 + 自動捲動 ===
// 改寫自 timeline.js:128-152 updateTimelinePlayhead ≈ video-edit-prototype.js:1847-1866 updatePlayhead。
// 只負責「播放頭幾何」本體；呼叫端自己的額外行為（podcast 的 playing-class 補回、
// 影片的單邊標記時重畫選區）留在呼叫端，不搬進來。
export function positionPlayhead({
  t,
  t0 = 0,
  total,
  elPh,
  elGrip = null,
  elScroll = null,
  elTl = null,
  margin = 40,
}) {
  const pct = Math.max(0, Math.min(100, timeToPct(t, t0, total)));
  if (elPh) elPh.style.left = `${pct}%`;
  if (elGrip) elGrip.style.left = `${pct}%`;
  if (elScroll && elTl && elTl.offsetWidth > elScroll.clientWidth + 1) {
    const x = (pct / 100) * elTl.offsetWidth;
    if (
      x < elScroll.scrollLeft + margin ||
      x > elScroll.scrollLeft + elScroll.clientWidth - margin
    ) {
      elScroll.scrollLeft = x - elScroll.clientWidth / 2;
    }
  }
  return pct;
}

// === 縮放 + 錨點保留數學 ===
// 改寫自 timeline.js:301-326 setTlZoom ≈ video-edit-prototype.js:1939-1956 setZoom。
// 純函式：夾制 z、算錨點 frac、寫入新 zoom 值、套寬度、重算 scrollLeft。
// 呼叫端各自的收尾（podcast：drawTlWaveform + localStorage 持久化；
// 影片：drawWaveform + render()）留在呼叫端注入，不搬進來 —— 兩邊順序都是
// 「setZoomValue → applyWidth → 捲動 → 收尾」，這裡保證前三步順序一致。
// 回傳 null 代表 z 沒有實際變化（呼叫端應跳過收尾，比照原本的提前 return）。
export function applyTimelineZoom({
  z,
  currentZoom,
  zoomMin,
  zoomMax,
  scroll,
  tl,
  anchorClientX = null,
  setZoomValue,
  applyWidth,
}) {
  z = Math.max(zoomMin, Math.min(zoomMax, z));
  if (Math.abs(z - currentZoom) < 1e-6) return null;

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

  setZoomValue(z);
  applyWidth();

  if (scroll && tl) {
    const newW = tl.offsetWidth || scroll.clientWidth * z;
    scroll.scrollLeft = frac * newW - anchorOffset;
  }
  return z;
}

// === 靜音參考帶 ===
// 改寫自 timeline.js:249-277 drawSilenceGuides ≈ video-edit-prototype.js:305-331 drawSilenceGuides。
// 只給眼睛對齊，不做吸附（吸附已於 2026-08 從 podcast 側移除，此檔不重新引入）。
export function drawSilenceGuidesCore({
  ctx,
  wf,
  t0 = 0,
  total,
  backW,
  backH,
  zoom,
  minZoom = 2,
  minGapPx = 5,
  dimColor,
}) {
  const sil = wf && wf.silences;
  if (!Array.isArray(sil) || !sil.length) return;
  if (zoom < minZoom) return;

  const dpr = window.devicePixelRatio || 1;
  const minGap = minGapPx * dpr;
  const x = (t) => timeToX(t, t0, total, backW);
  const edgeW = Math.max(1, Math.round(dpr));

  ctx.save();
  let lastEdge = -Infinity;
  for (const s of sil) {
    if (!Array.isArray(s) || s.length < 2) continue;
    const x0 = x(s[0]);
    const x1 = x(s[1]);
    if (x1 < 0 || x0 > backW) continue; // 不在可視範圍
    if (x0 - lastEdge < minGap && x1 - lastEdge < minGap) continue;

    ctx.fillStyle = dimColor;
    ctx.globalAlpha = 0.08;
    ctx.fillRect(x0, 0, Math.max(1, x1 - x0), backH);
    ctx.globalAlpha = 0.35;
    ctx.fillRect(x0, 0, edgeW, backH);
    ctx.fillRect(x1 - edgeW, 0, edgeW, backH);
    lastEdge = x1;
  }
  ctx.restore();
}

// === 波形繪製 ===
// 改寫自 timeline.js:179-243 drawTlWaveform ≈ video-edit-prototype.js:242-300 drawWaveform。
// 資料來源 /api/waveform 的 schema 已相同（peaks/peak_max/bucket_ms/silences），
// 這裡只管「畫」，不管「抓」——抓取與快取（loadWaveform / state.waveform 何時填入）留在呼叫端。
export function drawWaveformCore({
  tl,
  canvas,
  wf,
  t0 = 0,
  total,
  maxPx = 16384,
  accentColor,
  silence = null,
}) {
  if (!tl || !canvas || !wf || !wf.peaks || !wf.peaks.length) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const cssW = tl.clientWidth;
  const cssH = tl.clientHeight;
  if (!(total > 0) || cssW < 2 || cssH < 2) return;

  // 背板尺寸吃 devicePixelRatio 求銳利，但封頂避免高倍率下超出 canvas 限制而整片空白
  const dpr = window.devicePixelRatio || 1;
  const backW = Math.min(Math.max(1, Math.round(cssW * dpr)), maxPx);
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
    let k0 = Math.floor(pxToTime(x, backW, t0, total) / secPerBucket);
    let k1 = Math.floor(pxToTime(x + 1, backW, t0, total) / secPerBucket);
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
  ctx.fillStyle = accentColor;
  ctx.globalAlpha = 0.4;
  ctx.fill();
  ctx.globalAlpha = 1;

  if (silence) {
    drawSilenceGuidesCore({ ctx, wf, t0, total, backW, backH, ...silence });
  }
}

// === 標題卡：自動分軌（T4）===
// 逐字搬自 video-edit-prototype.js 舊版 assignCardLanes —— 純函式，只讀
// c.id/c.start/c.end，跟 t0/total 完全無關，兩模式可直接共用同一份。
// 貪婪區間分割：依 start 排序後逐一分配，找第一條「上一張卡已播完」的車道
// 塞進去，找不到就開新車道。用小 epsilon 容忍浮點誤差。
export const CARD_LANE_H = 36; // 車道高度（不含上下留白）
export const CARD_LANE_PAD = 4; // 軌道上下留白
export const CARD_LANE_GAP = 3; // 車道之間的視覺間隙
// 只有 1 車道時，track 高度＝ 1*CARD_LANE_H + 2*CARD_LANE_PAD = 44px。

export function assignCardLanes(cards) {
  const sorted = cards.slice().sort((a, b) => a.start - b.start);
  const laneEnds = []; // 每個車道目前排到的結束時間
  const laneOf = new Map(); // card.id → 車道序號
  const EPS = 1e-6;
  for (const c of sorted) {
    let lane = laneEnds.findIndex((end) => c.start >= end - EPS);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(c.end);
    } else {
      laneEnds[lane] = c.end;
    }
    laneOf.set(c.id, lane);
  }
  return { laneOf, nLanes: laneEnds.length };
}

// === 標題卡：軌道靜態渲染（T4）===
// 改寫自 video-edit-prototype.js 舊版 renderCardTrack —— 座標從「除以
// state.duration」參數化成 t0/total（t0=0 時算出來的數字與舊版逐位元相同）。
// D2：cut/partial 狀態不在此判斷，一律透過 getStatus(c) 注入（video 傳真正的
// cardDropped/subStatus 邏輯，podcast 傳一律回 null 的 no-op）。
// D2：cardTitle 內含 D2 相關文案（剪除提示），因此完全交給呼叫端的 getTitle(c)。
// 拖曳與雙擊改字皆為選用 callback（bindDrag/onDblClick/renderEditingCard），
// podcast 端可以只傳 bindDrag，略過雙擊改字與 editingId，維持「原型級展示＋
// 拖曳」的範圍，不做逐字編輯 UI。
export function renderCardTrackCore({
  track,
  cards,
  t0 = 0,
  total,
  selectedId = null,
  editingId = null,
  getStatus, // (c) => "cut" | "partial" | null
  getBadgeText, // (c) => string
  getCardText, // (c) => string
  getTitle, // (c) => string（title tooltip）
  onSelect, // (id) => void
  onDblClick = null, // (id) => void，可省略
  bindDrag, // (el, c, mode) => void
  renderEditingCard = null, // (track, c, top, laneHeight) => void，可省略
  renderFloorSec = 0.3, // 靜態渲染的寬度下限（秒），對齊舊版 Math.max(0.3, ...)
  onAfterRender = null, // () => void，渲染完的收尾（video 用來重算右欄標記等）
}) {
  if (!track) return;
  track.querySelectorAll(".vt-card, .vt-sub-input").forEach((n) => {
    if (n.isConnected) n.remove();
  });
  // 自動車道：每次重繪都重算，不重疊時自然收回單列
  const { laneOf, nLanes } = assignCardLanes(cards);
  const effLanes = Math.max(1, nLanes);
  track.style.height = `${effLanes * CARD_LANE_H + 2 * CARD_LANE_PAD}px`;
  cards.forEach((c) => {
    const lane = laneOf.get(c.id) || 0;
    const top = lane * CARD_LANE_H + CARD_LANE_PAD;
    const laneHeight = CARD_LANE_H - CARD_LANE_GAP;
    if (editingId === c.id && renderEditingCard) {
      renderEditingCard(track, c, top, laneHeight);
      return;
    }
    const el = document.createElement("div");
    const cut = getStatus(c);
    el.className =
      "vt-card" +
      (selectedId === c.id ? " is-selected" : "") +
      (cut === "cut" ? " is-cut" : cut === "partial" ? " is-partial" : "");
    el.style.left = `${timeToPct(c.start, t0, total)}%`;
    el.style.width = `${durToPct(Math.max(renderFloorSec, c.end - c.start), total)}%`;
    el.style.top = `${top}px`;
    el.style.height = `${laneHeight}px`;
    el.title = getTitle(c);
    const badge = document.createElement("span");
    badge.className = "vt-card-badge";
    badge.textContent = getBadgeText(c);
    el.appendChild(badge);
    const txt = document.createElement("span");
    txt.className = "vt-card-txt";
    txt.textContent = getCardText(c);
    el.appendChild(txt);
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      onSelect(c.id);
    });
    if (onDblClick) {
      el.addEventListener("dblclick", (ev) => {
        ev.stopPropagation();
        onDblClick(c.id);
      });
    }
    // 兩端各一個把手改長度，其餘區域拖整塊平移。卡太窄時兩個把手會把整塊
    // 佔滿 —— 那就別畫把手，先讓它拖得動；要改長度可以放大時間軸再拖。
    const wpx = durToPct(c.end - c.start, total) * 0.01 * track.clientWidth;
    if (wpx >= 24)
      ["l", "r"].forEach((side) => {
        const h = document.createElement("div");
        h.className = "vt-card-h is-" + side;
        h.title = side === "l" ? "拖曳改進點" : "拖曳改出點";
        el.appendChild(h);
        bindDrag(h, c, side);
      });
    bindDrag(el, c, "move");
    track.appendChild(el);
  });
  if (onAfterRender) onAfterRender();
}

// === 標題卡：時間軸拖曳（T4）===
// 改寫自 video-edit-prototype.js 舊版 bindCardTimeDrag —— 座標從
// state.duration 參數化成 t0/total；dt 是位移量，t0 不影響位移計算，
// 只有 clamp 的上下界要跟著 t0 平移。
// D2：不做 cut/partial 判斷（本函式完全不碰卡的剪除狀態）。
// 復原（undo）與拖曳結束後的收尾一律透過 onDragTick/onDragEnd 注入 ——
// video 傳真正的 pushUndo/endUndoGroup/renderCardTrack 等，podcast 目前
// 不掛進真正的 undo 堆疊（標題卡是原型級展示，不算「未存檔變更」）。
export function bindCardTimeDragCore(
  el,
  c,
  mode,
  {
    trackEl,
    t0 = 0,
    total,
    minDur = 0.3,
    getTitle,
    onPointerDown = null,
    onDragTick = null,
    onDragEnd = null,
  },
) {
  el.addEventListener("pointerdown", (e) => {
    if (e.button != null && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    if (onPointerDown) onPointerDown(c);
    const rect = trackEl.getBoundingClientRect();
    const x0 = e.clientX;
    const s0 = c.start;
    const e0 = c.end;
    const dur = e0 - s0;
    const block = el.closest(".vt-card") || el;
    block.classList.add("is-dragging");
    const move = (ev) => {
      if (onDragTick) onDragTick(c);
      const dt = ((ev.clientX - x0) / rect.width) * total;
      if (mode === "move") {
        c.start = clamp(s0 + dt, t0, Math.max(t0, t0 + total - dur));
        c.end = c.start + dur;
      } else if (mode === "l") {
        c.start = clamp(s0 + dt, t0, e0 - minDur);
        c.end = e0;
      } else {
        c.start = s0;
        c.end = clamp(e0 + dt, s0 + minDur, t0 + total);
      }
      block.style.left = `${timeToPct(c.start, t0, total)}%`;
      block.style.width = `${durToPct(c.end - c.start, total)}%`;
      block.title = getTitle(c);
    };
    const up = () => {
      block.classList.remove("is-dragging");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (onDragEnd) onDragEnd(c);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
}

// === 播放頭刮動（drag-to-scrub，B3b/梯次 B）===
// 改寫自 video-edit-prototype.js:1833-1858 bindPlayheadScrub —— 把「換算時間」與「seek」
// 兩個與模式相關的行為參數化成 callback，本函式只管拖曳序列（down→多次 move→up）本身。
// 純函式：不 import 任何 app.js/v-e-p.js 符號，grip 元素、時間換算、seek、可否刮動判斷
// 全由呼叫端注入，與剪除語意（cuts/deletions）零耦合——只設播放時間，不動任何刪段狀態。
// 失敗路徑不靜默成「假成功」：canScrub 回 false（無影片/duration 未知）時直接 return 不啟動
// 拖曳，不拋例外也不假裝有 seek；seek callback 自己負責 clamp 與更新播放頭。
export function bindPlayheadScrubCore(
  grip,
  { timeFromEvent, seek, canScrub = null, onStart = null, onEnd = null },
) {
  if (!grip) return;
  grip.addEventListener("pointerdown", (e) => {
    if (e.button != null && e.button !== 0) return;
    // 無影片 / duration 未知 → 不啟動刮動（明確 guard，不是靜默 fallback 成成功）
    if (canScrub && !canScrub()) return;
    e.preventDefault();
    e.stopPropagation();
    grip.classList.add("is-scrubbing");
    // 某些環境（自動化、觸控筆切換）setPointerCapture 會丟例外，抓不到就算了——
    // 監聽掛在 window 上，拖出把手外一樣收得到。
    try {
      grip.setPointerCapture(e.pointerId);
    } catch (_) {}
    if (onStart) onStart();
    seek(timeFromEvent(e)); // 按下當下就先跳一次，不用等第一個 move
    const move = (ev) => seek(timeFromEvent(ev));
    const up = () => {
      grip.classList.remove("is-scrubbing");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (onEnd) onEnd();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
}
