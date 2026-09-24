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

// === 字幕樣式：ASS → CSS 的單一換算來源（W2）===
// 原本有兩套在管「字幕樣式長怎樣」：影片原型 applyStyle() 做完整換算（顏色/描邊/
// 粗體/底色塊），podcast 側只算字級、其餘寫死在 app.css → 預覽 ≠ 成品。
// 本段把換算收成一份純函式（不碰 DOM、不吃外部可變狀態），兩邊都改吃它。
// 兩邊的 style 物件欄位不同：影片面板存 hex（color picker 的值），
// episode.yaml 存 ASS &H00BBGGRR — 故 buildSubtitleCss 兩種都收。

/** hex #RRGGBB → ASS &H00BBGGRR（讓面板數值看起來跟後端同格式）。 */
export function hexToAss(hex) {
  const h = (hex || "#000000").replace("#", "");
  const r = h.slice(0, 2);
  const g = h.slice(2, 4);
  const b = h.slice(4, 6);
  return `&H00${b}${g}${r}`.toUpperCase();
}

/** 字型排前，後面墊 CJK fallback，無該字型時仍看得到字。 */
export function fontStack(name) {
  const cjk =
    '"Noto Sans TC", "PingFang TC", "Hiragino Sans GB", "Microsoft JhengHei", sans-serif';
  return `"${name}", ${cjk}`;
}

/** 用多向 text-shadow 疊出 ASS 描邊 + 陰影。 */
export function buildOutlineShadow(color, outlinePx, shadowPx) {
  const parts = [];
  if (outlinePx > 0) {
    const n = 12;
    for (let k = 0; k < n; k++) {
      const a = (k / n) * Math.PI * 2;
      parts.push(
        `${(Math.cos(a) * outlinePx).toFixed(2)}px ${(Math.sin(a) * outlinePx).toFixed(2)}px 0 ${color}`,
      );
    }
  }
  if (shadowPx > 0) {
    // ASS 陰影固定黑、往右下（此為字幕內容陰影，非 UI chrome）
    parts.push(
      `${shadowPx.toFixed(2)}px ${shadowPx.toFixed(2)}px ${(shadowPx * 1.2).toFixed(2)}px rgba(0,0,0,0.9)`,
    );
  }
  return parts.length ? parts.join(", ") : "none";
}

/** ASS &H00BBGGRR（或 &HBBGGRR）→ hex #RRGGBB；解析不出來就回 fallback。 */
export function assToHex(ass, fallback = "#ffffff") {
  const m = String(ass || "").match(/^&H([0-9a-fA-F]{2})?([0-9a-fA-F]{6})$/);
  if (!m) return fallback;
  const bgr = m[2];
  const b = bgr.slice(0, 2);
  const g = bgr.slice(2, 4);
  const r = bgr.slice(4, 6);
  return `#${r}${g}${b}`.toLowerCase();
}

/** style 物件取色：影片面板存 *_hex，episode.yaml 存 ASS 字串，兩種都接。 */
function styleColour(style, hexKey, assKey, fallback) {
  const hex = style && style[hexKey];
  if (typeof hex === "string" && hex) return hex;
  return assToHex(style && style[assKey], fallback);
}

/**
 * ASS 字幕樣式 → 預覽用 CSS 屬性表（純運算）。
 * scale = 預覽高 / 輸出高，讓預覽的字跟畫面比例等同最終燒錄結果。
 * 回傳的 key 就是 element.style 的 key，呼叫端逐一指派即可。
 */
export function buildSubtitleCss(style, scale) {
  const st = style || {};
  const px = Math.max(9, (Number(st.font_size) || 0) * scale);
  const primary = styleColour(st, "primary_colour_hex", "primary_colour", "#ffffff");
  const outlineCol = styleColour(st, "outline_colour_hex", "outline_colour", "#000000");
  const css = {
    fontFamily: fontStack(st.font_name),
    fontSize: `${px.toFixed(1)}px`,
    fontWeight: Number(st.bold) ? "700" : "400",
    color: primary,
  };
  if (Number(st.border_style) === 3) {
    // 不透明底色塊：底色用描邊色，取消描邊
    css.background = outlineCol;
    css.padding = `${(px * 0.1).toFixed(1)}px ${(px * 0.32).toFixed(1)}px`;
    css.borderRadius = "2px";
    css.textShadow = "none";
  } else {
    css.background = "transparent";
    css.padding = "0";
    css.borderRadius = "0";
    // 略放大讓小預覽看得見描邊（與影片原型既有係數一致）
    css.textShadow = buildOutlineShadow(
      outlineCol,
      (Number(st.outline) || 0) * scale * 1.2,
      (Number(st.shadow) || 0) * scale * 1.2,
    );
  }
  return css;
}

/**
 * SSA v3 alignment → 垂直落點分類。
 * 6=頂部置中、10=畫面正中央，其餘（含預設 2 與沒填）都是底部置中。
 * ⚠️ v3 不是 numpad：5 在 v3 是左上角，不要當成「正中央」。
 */
export function subtitleAlignmentBucket(alignment) {
  const a = Number(alignment);
  if (a === 6) return "top";
  if (a === 10) return "middle";
  return "bottom";
}

/**
 * px 座標系的字幕定位（影片原型用：預覽容器相對 video 元素絕對定位）。
 * marginPx 為已乘過 scale 的邊距；alignment=10 時正值代表從中心往下偏移。
 */
export function subtitlePositionCss(alignment, marginPx) {
  const bucket = subtitleAlignmentBucket(alignment);
  if (bucket === "top") {
    return { top: `${marginPx}px`, bottom: "auto", transform: "none" };
  }
  if (bucket === "middle") {
    return {
      top: "50%",
      bottom: "auto",
      transform: `translateY(calc(-50% + ${marginPx}px))`,
    };
  }
  return { top: "auto", bottom: `${marginPx}px`, transform: "none" };
}

// ─────────────────────────────────────────────────────────────────────────────
// 剪除語意的邊界換算（B1 專梯）
//
// 持久化只留一份真相：episode.yaml 的 `cuts`（時間版區間，_v2.srt 磁碟軸）。
// podcast 編輯器內部仍用 `state.deletions`（Set<卡 key>）當操作介面，只在
// 「載入／存檔」這兩個邊界用下面兩個純函式換算——形狀對齊鏡頭切換點的既有前例
// （時間版為真相、前端維持卡 key 介面，見 episode_io.transitions_to_card_mapping）。
//
// 呼叫端負責時間軸對齊：傳進來的 rows 與 cuts 必須在**同一條軸**上
// （app.js 內部是 cam A 軸、磁碟是外接音檔軸，差一個 audioSyncOffset）。
// ─────────────────────────────────────────────────────────────────────────────

/** 兩個時間是否在容差內相等。 */
function _near(a, b, tol) {
  return Math.abs(a - b) <= tol;
}

/**
 * 卡 key 集合 → 時間版 cuts（逐卡一段、依 start 排序）。
 *
 * **刻意不預先合併**（連續卡也不併）：舊 deletions 路徑在後端就是
 * `_from_idx()` 逐卡吐區間，這裡逐位元照做，`cut_intervals_from_cfg` 拿到的
 * `intervals` 才會與舊路完全相同——包含 `cut_pad=0`（後端不合併）那條分支。
 * 要不要把連刪跨停頓併成整段，仍由後端 `_pad_and_merge_cuts` 單一決定。
 *
 * @param {Set|Array} keys 被刪的卡 key（int 或 "idx:part"）
 * @param {Array<{key:*, start:number, end:number}>} rows 展開後的卡（expandedCards()）
 * @returns {Array<[number, number]>}
 */
export function cardKeysToCuts(keys, rows) {
  const set = keys instanceof Set ? keys : new Set(keys || []);
  return (rows || [])
    .filter((r) => set.has(r.key))
    .map((r) => [Number(r.start), Number(r.end)])
    .sort((a, b) => a[0] - b[0]);
}

/**
 * 時間版 cuts → { keys, foreign }。
 *
 * 一段 cut 只有在「恰好等於一串連續卡的外緣」時才換算成卡 key；否則原樣留在 foreign
 * （影片模式切出來的任意區間、或跨了非連續卡的段落）。foreign 由呼叫端原樣送回存檔，
 * **絕不擴寬成整張卡、也絕不丟掉**——卡層 UI 表達不了不代表可以竄改資料。
 *
 * @returns {{keys: Array, foreign: Array<[number, number]>}}
 */
export function cutsToCardSelection(cuts, rows, tol = 0.02) {
  const all = (rows || [])
    .map((r) => ({ key: r.key, start: Number(r.start), end: Number(r.end) }))
    .sort((a, b) => a.start - b.start);
  const keys = [];
  const foreign = [];
  for (const c of cuts || []) {
    const s = Number(Array.isArray(c) ? c[0] : c.start);
    const e = Number(Array.isArray(c) ? c[1] : c.end);
    if (!(e > s)) continue; // 零長度／反置：丟掉（後端存檔端也會丟）
    const covered = all.filter((r) => r.start >= s - tol && r.end <= e + tol);
    let aligned = covered.length > 0;
    if (aligned) {
      // 外緣要對齊，中間不能有真停頓（否則換算回來會把停頓還原、與原 cut 不等價）
      if (!_near(covered[0].start, s, tol) || !_near(covered[covered.length - 1].end, e, tol)) {
        aligned = false;
      } else {
        for (let i = 1; i < covered.length; i++) {
          if (covered[i].start > covered[i - 1].end + tol) {
            aligned = false;
            break;
          }
        }
      }
    }
    if (aligned) {
      for (const r of covered) keys.push(r.key);
    } else {
      foreign.push([s, e]);
    }
  }
  foreign.sort((a, b) => a[0] - b[0]);
  return { keys, foreign };
}

/**
 * 刪除區間的「延伸 + 合併」——與後端 `assemble._pad_and_merge_cuts` 同一套規則。
 *
 * 這是逐行對照的移植版（差分測試 tests/test_cut_merge_frontend_parity.py 逐位元比對），
 * 存在的理由是：預覽跳段與最終輸出必須是**同一個演算法**。之前前端另寫了一套
 * （無 pad、合併門檻 0.05、只夾下一張保留卡起點），預覽每段都比輸出短 cut_pad 秒／側，
 * 使用者看到的跳段時機跟成品對不上。
 *
 * 規則（與後端逐條相同）：
 * - `pad <= 0` → 原樣返回（不合併），維持後端的向後相容分支。
 * - 正規化：修反置 (start>end)、丟零長度。
 * - 保留卡 = 未被任一刪段「整段涵蓋」的卡；部分被切的卡仍算保留。
 * - 先併「中間沒有保留卡語音」的相鄰刪段（連刪跨停頓也併）。
 * - 每段往兩側最多吃 pad 秒，但夾在鄰近保留卡的語音邊界內；某側沒有鄰卡則該側不外吃。
 * - 延伸後重疊/相鄰再合併一次。
 *
 * @param {Array<[number, number]>} intervals 刪除區間（與 cards 同一條時間軸）
 * @param {Array<{start:number, end:number}>} cards 全部字幕卡（用來算保留卡邊界）
 * @param {number} pad 每側最多吃掉的雜音秒數（episode.yaml 的 cut_pad）
 * @returns {Array<[number, number]>} 排序好的區間
 */
export function padAndMergeCuts(intervals, cards, pad) {
  const byStart = (a, b) => a[0] - b[0] || a[1] - b[1];
  const src = (intervals || []).map(([s, e]) => [Number(s), Number(e)]).sort(byStart);
  if (!(pad > 0) || !src.length) return src;

  // 正規化：修反置、丟零長度（手動 cuts 打錯時的防呆）
  const norm = src
    .filter(([a, b]) => a !== b)
    .map(([a, b]) => [Math.min(a, b), Math.max(a, b)])
    .sort(byStart);
  if (!norm.length) return [];

  const kept = (cards || [])
    .map((c) => [Number(c.start), Number(c.end)])
    .filter(([cs, ce]) => !norm.some(([s, e]) => s - 1e-6 <= cs && ce <= e + 1e-6));

  // 先併「相鄰刪段之間沒有保留卡語音」的區間（flush-adjacent 的連續字幕也擋得下）
  const pre = [[...norm[0]]];
  for (const [s, e] of norm.slice(1)) {
    const pe = pre[pre.length - 1][1];
    const gapHasKept = kept.some(([cs, ce]) => cs < s - 1e-6 && ce > pe + 1e-6);
    if (s <= pe + 1e-6 || !gapHasKept) pre[pre.length - 1][1] = Math.max(pe, e);
    else pre.push([s, e]);
  }

  // 各段往前後延伸 pad，夾在保留卡語音邊界內
  const out = [];
  for (const [s, e] of pre) {
    const lefts = kept.filter(([cs]) => cs < s - 1e-6).map(([, ce]) => Math.min(ce, s));
    const rights = kept.filter(([, ce]) => ce > e + 1e-6).map(([cs]) => Math.max(cs, e));
    const leftLimit = lefts.length ? Math.max(...lefts) : s;
    const rightLimit = rights.length ? Math.min(...rights) : e;
    const ns = Math.max(s - pad, leftLimit, 0);
    const ne = Math.min(e + pad, rightLimit);
    out.push([ns, Math.max(ne, ns)]);
  }

  // 延伸後若重疊/相鄰 → 合併
  out.sort(byStart);
  const merged = [out[0]];
  for (const [s, e] of out.slice(1)) {
    const last = merged[merged.length - 1];
    if (s <= last[1] + 1e-6) last[1] = Math.max(last[1], e);
    else merged.push([s, e]);
  }
  return merged;
}
