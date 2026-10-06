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
import {
  applyTimelineZoom,
  positionPlayhead,
  drawWaveformCore,
  renderCardTrackCore,
  bindCardTimeDragCore,
  bindPlayheadScrubCore,
} from "./timeline-core.js";

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
  // 框選 marquee（B3c）：綁在持久的 #card-timeline 元素上（renderCardTimeline 只清 innerHTML、
  // 不換元素），故一次綁定即可，用旗標擋每次 render 重複疊監聽。
  if (!tl.__ptMarqueeBound) {
    tl.__ptMarqueeBound = true;
    bindTimelineMarquee(tl);
  }
  const rendered =
    state.needsTranscribe || !state.cards.length ? [] : expandedCards();
  if (!rendered.length) {
    wrap.hidden = true;
    tl.innerHTML = "";
    setCardTrackVisible(false);
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
      // 剛結束整段平移時，滑鼠放開會補一個 click —— 這種 click 不跳播放頭（B3b）
      if (block.__ptMoved) {
        block.__ptMoved = false;
        return;
      }
      // 讀 dataset 不讀 r.start：時間改過之後這個 closure 就過期了
      $("#video").currentTime = Number(block.dataset.start);
    });
    // 整段平移（B3b）：拖卡塊本體（非兩端把手）水平移動整句時間，長度不變、夾在鄰句之間。
    // 純點擊（未超過門檻）不會啟動平移、不 pushUndo、不重算 —— 交給上面的 click 跳播放頭。
    block.addEventListener("pointerdown", (e) => {
      if (e.target.classList.contains("tl-handle")) return; // 兩端把手自己處理
      if (e.button != null && e.button !== 0) return;
      startTimelineDrag(e, r, "move");
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
  // 播放頭刮動把手（B3a）：跟播放頭同位、命中區加寬到 13px 好抓（播放頭本體 2px 且
  // pointer-events:none 抓不到）。無 .tl-playhead-grip CSS（本梯只動 JS 兩檔），
  // 幾何用 inline style 給足。z-index 高於字幕塊 → 播放頭那 13px 帶改由刮動接管
  // （代價：正落在播放頭下的那顆卡把手在該帶內點不到，是刻意取捨，見計畫 B3a）。
  const grip = document.createElement("div");
  grip.className = "tl-playhead-grip";
  grip.id = "tl-playhead-grip";
  grip.title = "拖曳刮動播放時間";
  grip.style.cssText =
    "position:absolute;top:0;bottom:0;width:13px;margin-left:-6px;z-index:6;cursor:ew-resize;background:transparent;touch-action:none;";
  tl.appendChild(grip);
  bindPodcastScrub(grip);
  _applyTlZoomWidth();
  updateTimelinePlayhead($("#video").currentTime);
  drawTlWaveform(); // 若波形已載入就即刻畫；否則下面背景載入回來會再畫
  // 標題卡軌（T4）：與 #card-timeline 共用 t0/total。podcast 預設「關」（index.html 寫死
  // hidden，對齊 D1「podcast 預設關、影片預設開」）——這裡「不」強制開啟；只有軌已開啟時
  // （影片模式 markup 預設開、或 podcast 走查以 __ptSetCardTrackVisible 開）才跟著重畫，
  // 維持一起縮放/對齊。renderPodcastCardTrack 內部對 track.hidden 自我防呆，關閉時為 no-op。
  renderPodcastCardTrack();
  // 背景載波形（首次要 ffmpeg 解碼，可能 20~40s）：不 await、不擋首屏。只抓一次——
  // flag 擋住每次 render 重抓；換集會整頁重載、state 重置。轉錄前（沒卡）不抓。
  if (!state.waveform && !state._wfFetching && !state.needsTranscribe) {
    state._wfFetching = true;
    loadWaveform().finally(() => {
      state._wfFetching = false;
    });
  }
}

// 標題卡軌開關 + 渲染（T3 開了空容器，T4 本次接上實際內容）：
// flag 就是 DOM 的 hidden 屬性本身（無需另開 state 布林）：關閉時 [hidden] 對應
// display:none，完全不佔版面；#card-timeline 與波形/播放頭的量測行為不受影響。
// podcast 預設關（index.html 該節點已寫死 hidden）；影片模式的對應軌預設開，
// 由 video-edit-prototype.html 自己的 markup 決定，不受本函式影響。
function setCardTrackVisible(show) {
  const t = $("#tl-card-track");
  if (t) t.hidden = !show;
  if (show) renderPodcastCardTrack();
}

// 標題卡軌實際渲染（T4，docs/plans/2026-09-23-unified-timeline-core.md）：
// 與影片模式共用 timeline-core.js 的 renderCardTrackCore/bindCardTimeDragCore，
// 沿用 #card-timeline 已經算好的 t0/total（同一條時間軸，同一套座標系）。
// D2：podcast 沒有 cuts，getStatus 一律回 null，不判斷「剪掉/部分剪掉」。
// 本函式只讀 state.titleCards 畫軌，不寫任何 state、不呼叫 /api/save（純渲染）。
// 持久化改由 B2 負責：buildSavePayload 送 title_cards、loadEpisodeState 讀回（不再是丟棄式）。
// 標題卡目前沒有文字編輯 UI（onDblClick/renderEditingCard 不注入）。
let _ptSelectedId = null;

function renderPodcastCardTrack() {
  const track = $("#tl-card-track");
  const tl = $("#card-timeline");
  if (!track || !tl || track.hidden || !tl.dataset.total) return;
  const t0 = parseFloat(tl.dataset.t0 || "0");
  const total = parseFloat(tl.dataset.total || "1");
  const label = (c) => `${fmtTimeCard(c.start)} – ${fmtTimeCard(c.end)}`;
  renderCardTrackCore({
    track,
    cards: state.titleCards,
    t0,
    total,
    selectedId: _ptSelectedId,
    getStatus: () => null,
    getBadgeText: () => "卡",
    getCardText: (c) => c.text || "",
    getTitle: label,
    onSelect: (id) => {
      _ptSelectedId = id;
      renderPodcastCardTrack();
    },
    bindDrag: (el, c, mode) =>
      bindCardTimeDragCore(el, c, mode, {
        trackEl: track,
        t0,
        total,
        getTitle: label,
        onDragEnd: () => renderPodcastCardTrack(),
      }),
  });
}

// 走查用：程式化選取（等同點卡片）。
function selectTitleCard(id) {
  _ptSelectedId = id;
  renderPodcastCardTrack();
}

// 播放頭刮動把手綁定（B3a）：把「換算時間 / seek / 可否刮動」注入共用核心
// bindPlayheadScrubCore。與剪除語意零耦合——只設 #video.currentTime 並更新播放頭。
// timeFromEvent 每次都讀 tl 的 live dataset（t0/total 只在整條重建時才變、重建會重綁），
// 座標換算與 podcast 的字幕窗一致（t0 可能非 0）。
function bindPodcastScrub(grip) {
  const tl = $("#card-timeline");
  bindPlayheadScrubCore(grip, {
    // duration 未知（無影片/尚未載入）→ 不刮動；seekable 由走查在 CDP 端先驗（Range 支援）
    canScrub: () => {
      const v = $("#video");
      return !!(v && isFinite(v.duration) && v.duration > 0);
    },
    timeFromEvent: (e) => {
      const rect = tl.getBoundingClientRect();
      const t0 = Number(tl.dataset.t0) || 0;
      const total = Number(tl.dataset.total) || 1;
      const t = t0 + ((e.clientX - rect.left) / rect.width) * total;
      return Math.max(t0, Math.min(t0 + total, t));
    },
    seek: (t) => {
      const v = $("#video");
      if (!v) return;
      v.currentTime = t;
      updateTimelinePlayhead(t); // 暫停時 timeupdate 不一定即時，主動同步播放頭幾何
    },
  });
}

// 框選 marquee（B3c，docs/plans/2026-09-23-unified-timeline-core.md）：
// 在 #card-timeline 背景橫拖一個矩形，放開時把「被框到時間範圍涵蓋的字幕卡」idx 併入既有
// state.deletions。刻意不碰 cuts、不動 proofread、零後端改動——純前端把選取換算成既有刪段語意。
//
// 與 B3a 刮動 / B3b 整段平移 / 端點拖曳共存的關鍵：字幕塊與端點把手的 pointerdown 都會
// stopPropagation（startTimelineDrag），播放頭刮把手是自己的子元素——所以只在
// `e.target === tl`（時間軸背景本身：上下各 3px 帶 + 卡與卡之間的空隙）啟動框選，
// 三者互不搶事件。代價：框選只能從背景帶起手（人手偏窄，但 CDP 座標派發穩定；最小版取捨）。
//
// 選取判準採「相交」：卡的時間區間與框選區間有交集即算涵蓋（`start < hi && end > lo`），
// 比「完全包住」更貼近「框到就選」的直覺。新增卡（key `new:*`，尚未進 _v2.srt）不納入——
// 它的刪除語意是從 state.newCards 移除、非進 deletions，最小版不動它。
// 合併是 union：只加不減（Set 自動去重），對齊計畫「併入既有 deletions」。
const _MARQUEE_MIN_PX = 4; // 位移小於此值 = 視為誤點，不選取（避免背景輕點就框到整批）

function bindTimelineMarquee(tl) {
  tl.addEventListener("pointerdown", (e) => {
    if (e.button != null && e.button !== 0) return;
    // 只在時間軸背景起手：卡塊/把手已 stopPropagation，刮把手是子元素 target≠tl → 天然排除。
    if (e.target !== tl) return;
    const rect = tl.getBoundingClientRect();
    const total = Number(tl.dataset.total) || 0;
    const t0 = Number(tl.dataset.t0) || 0;
    if (total <= 0) return; // 尚無時間軸資料（無卡/未載入）→ 不啟動，不假裝成功
    e.preventDefault();
    const x0 = e.clientX;
    const box = document.createElement("div");
    box.className = "tl-marquee";
    box.id = "tl-marquee";
    // 不動 CSS（本項只許改 app.js/timeline.js/api.js）→ 幾何與外觀走 inline style。
    // z-index 4：蓋在波形(3)/卡塊之上、播放頭(5)之下；pointer-events:none 不吃事件。
    box.style.cssText =
      "position:absolute;top:0;bottom:0;z-index:4;pointer-events:none;" +
      "background:var(--accent);opacity:0.22;border:1px solid var(--accent);box-sizing:border-box;";
    tl.appendChild(box);
    const pxToTime = (clientX) => {
      const frac = (clientX - rect.left) / rect.width;
      return Math.max(t0, Math.min(t0 + total, t0 + frac * total));
    };
    const paint = (clientX) => {
      const lo = Math.min(x0, clientX) - rect.left;
      const hi = Math.max(x0, clientX) - rect.left;
      const L = Math.max(0, Math.min(rect.width, lo));
      const R = Math.max(0, Math.min(rect.width, hi));
      box.style.left = `${L}px`;
      box.style.width = `${Math.max(0, R - L)}px`;
    };
    paint(x0);
    try {
      tl.setPointerCapture(e.pointerId);
    } catch (_) {}
    const move = (ev) => paint(ev.clientX);
    const up = (ev) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      box.remove();
      // 位移太小 = 誤點，不選取（避免背景一點就把整批卡吞進 deletions）
      if (Math.abs(ev.clientX - x0) < _MARQUEE_MIN_PX) return;
      const lo = pxToTime(Math.min(x0, ev.clientX));
      const hi = pxToTime(Math.max(x0, ev.clientX));
      // 相交即涵蓋；排除新增卡（key new:*，刪除語意不同）
      const keys = expandedCards()
        .filter(
          (r) =>
            !String(r.key).startsWith("new:") && r.start < hi && r.end > lo,
        )
        .map((r) => r.key);
      // 只有真的框到卡、且會新增至少一個尚未刪的 key 才 pushUndo + 重繪（失敗不靜默：
      // 框到空白 → 無事發生但也無錯誤狀態，符合「什麼都沒選到」的可見結果）。
      const added = keys.filter((k) => !state.deletions.has(k));
      if (!added.length) return;
      pushUndo();
      for (const k of added) state.deletions.add(k);
      rerenderEditState();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  });
}

// 三邊同步：影片時間 → 時間軸播放頭位置 + 高亮當前 block（縮放時自動捲到可見）
function updateTimelinePlayhead(t) {
  const tl = $("#card-timeline");
  const ph = $("#tl-playhead");
  if (!tl || !ph) return;
  const t0 = parseFloat(tl.dataset.t0 || "0");
  const total = parseFloat(tl.dataset.total || "1");
  // 幾何本體（pct 計算 + 自動捲動）抽到 timeline-core.js，與影片模式共用；
  // elGrip 讓刮動把手跟著播放頭同位移動（B3a）。
  positionPlayhead({
    t,
    t0,
    total,
    elPh: ph,
    elGrip: $("#tl-playhead-grip"),
    elScroll: tl.closest(".card-timeline-scroll"),
    elTl: tl,
  });
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
  const t0 = parseFloat(tl.dataset.t0 || "0");
  const total = parseFloat(tl.dataset.total || "1");
  const accent =
    getComputedStyle(document.documentElement)
      .getPropertyValue("--accent")
      .trim() || "#4a9eff";
  // 繪圖本體（含靜音參考帶）抽到 timeline-core.js，與影片模式共用；
  // 顏色/門檻值仍由呼叫端各自決定，不改變原本外觀。
  drawWaveformCore({
    tl,
    canvas,
    wf,
    t0,
    total,
    maxPx: TL_WAVE_MAX_PX,
    accentColor: accent,
    silence: {
      zoom: state.tlZoom,
      minZoom: TL_GUIDE_MIN_ZOOM,
      minGapPx: TL_GUIDE_MIN_GAP_PX,
      dimColor: "#9b9ba8", // --text-dim，刻意不用 accent（那是波形的顏色）
    },
  });
}

// 靜音區間的視覺參考線：只給眼睛對齊，不做任何吸附（吸附已於 2026-08 移除，見 onTimelineDragMove）。
const TL_GUIDE_MIN_ZOOM = 2; // 1×（總覽）時邊界密到變雜訊，放大後才顯示
const TL_GUIDE_MIN_GAP_PX = 5; // 兩條邊界靠太近就只畫前一條，避免糊成一片

// === 時間軸縮放（zoom + 橫向捲動）===
// #card-timeline 寬度 = zoom×100%（其容器 .card-timeline-scroll 提供橫向捲動）。
// 區塊仍用 left%/width% 定位 → 元素變寬時自動攤開，拖拉數學（吃 rect.width）不需改。
const TL_ZOOM_MIN = 1;
const TL_ZOOM_MAX = 60;
const TL_ZOOM_STEP = 1.6; // 每按一次 ＋/− 的倍率

function _applyTlZoomWidth() {
  // 縮放施加在「三軌共用容器」#tl-tracks 上（寬度＝zoom×100%），#card-timeline 與
  // #tl-card-track 都是 width:100% 子元素 → 任一縮放倍率下自動等寬對齊（與影片模式對
  // #vt-tracks 施加縮放同構）。#card-timeline 不再自己設 inline width，改吃 CSS 100%；
  // 因它 = 100% × #tl-tracks 寬 = zoom×scrollWidth，最終幾何與舊版逐像素相同，
  // 字幕塊/播放頭/波形量測零回歸。applyTimelineZoom 讀的 tl.offsetWidth（=#card-timeline）
  // 於 applyWidth 後 reflow 即反映新寬，錨點數學不變。
  const tracks = $("#tl-tracks");
  if (tracks) tracks.style.width = `${state.tlZoom * 100}%`;
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
  const scroll = $("#card-timeline-scroll");
  const tl = $("#card-timeline");
  // 夾制 + 錨點保留數學抽到 timeline-core.js，與影片模式共用；
  // 回傳 null 代表 z 沒有實際變化，比照原本提前 return（不重畫、不寫 localStorage）。
  const applied = applyTimelineZoom({
    z,
    currentZoom: state.tlZoom,
    zoomMin: TL_ZOOM_MIN,
    zoomMax: TL_ZOOM_MAX,
    scroll,
    tl,
    anchorClientX,
    setZoomValue: (v) => {
      state.tlZoom = v;
    },
    applyWidth: _applyTlZoomWidth,
  });
  if (applied == null) return;
  drawTlWaveform(); // 寬度變了 → 波形背板重算重畫（只在縮放時，一次）
  try {
    localStorage.setItem("edit.tlZoom", String(applied));
  } catch (_) {}
}

// edge：'start' | 'end'（拖端點改進出時間）| 'move'（B3b：整段平移）。
// 端點拖曳：e.currentTarget 是把手，block = 把手的父卡塊，立即 pushUndo + 建 readout。
// 整段平移：e.currentTarget 就是卡塊本體，pushUndo/readout 延到「真的移動超過門檻」才做
// （純點擊 = 只跳播放頭，不污染復原堆疊、不整條重算）。
function startTimelineDrag(e, r, edge) {
  e.preventDefault();
  e.stopPropagation();
  const isMove = edge === "move";
  const el = e.currentTarget; // 端點把手 or 卡塊本體
  const block = isMove ? el : el.parentElement;
  const tl = $("#card-timeline");
  const rect = tl.getBoundingClientRect();
  if (!isMove) pushUndo();
  _tlDrag = {
    key: r.key,
    edge,
    rect,
    total: Number(tl.dataset.total) || 1,
    t0: Number(tl.dataset.t0) || 0,
    tl,
    handle: el,
    block,
    x0: e.clientX,
    s0: r.start,
    e0: r.end,
    moved: false,
    undoPushed: !isMove,
  };
  try {
    el.setPointerCapture(e.pointerId);
  } catch (_) {}
  // 端點拖曳精確時間讀值：接到 body（fixed 定位，不受時間軸 overflow:hidden 裁切）。
  // 整段平移的 readout 改在 onTimelineDragMove 第一次真移動時才建（避免純點擊也閃一下）。
  if (!isMove) {
    let readout = document.getElementById("tl-drag-readout");
    if (!readout) {
      readout = document.createElement("div");
      readout.id = "tl-drag-readout";
      readout.className = "tl-drag-readout";
      document.body.appendChild(readout);
    }
    _tlDrag.readout = readout;
  }
  el.addEventListener("pointermove", onTimelineDragMove);
  el.addEventListener("pointerup", endTimelineDrag);
  el.addEventListener("pointercancel", endTimelineDrag);
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
  // 整段平移（B3b）：長度不變，整塊夾在鄰句之間（前句結束 ~ 後句開始），永不重疊、不換序。
  // 與剪除語意零耦合——只改 timing（cardTimings/newCard），不碰 deletions。
  if (edge === "move") {
    const { x0, s0, e0 } = _tlDrag;
    const dur = e0 - s0;
    const lo = prev ? prev.end : t0; // 不得壓過前一句結束
    const hi = next ? next.start : t0 + total; // 不得壓過後一句開始
    const dt = ((e.clientX - x0) / rect.width) * total;
    const ns = Math.max(lo, Math.min(hi - dur, s0 + dt));
    // 移動門檻：未超過 4px 視為點擊 → 不寫時間、不 pushUndo，交給 block 的 click 跳播放頭
    if (Math.abs(e.clientX - x0) > 4) {
      if (!_tlDrag.undoPushed) {
        pushUndo();
        _tlDrag.undoPushed = true;
      }
      _tlDrag.moved = true;
      block.__ptMoved = true;
    }
    if (!_tlDrag.moved) return;
    writeTime(ns, ns + dur);
    _tlSetBlockGeom(block, ns, ns + dur, t0, total);
    // readout 延到第一次真移動才建（見 startTimelineDrag）
    if (!_tlDrag.readout) {
      const ro = document.createElement("div");
      ro.id = "tl-drag-readout";
      ro.className = "tl-drag-readout";
      document.body.appendChild(ro);
      _tlDrag.readout = ro;
    }
    _tlDrag.readout.textContent = fmtTimeCard(ns);
    _tlDrag.readout.style.left = `${e.clientX}px`;
    _tlDrag.readout.style.top = `${e.clientY}px`;
    return;
  }
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
  const { handle, edge, moved } = _tlDrag;
  try {
    handle.releasePointerCapture(e.pointerId);
  } catch (_) {}
  handle.removeEventListener("pointermove", onTimelineDragMove);
  handle.removeEventListener("pointerup", endTimelineDrag);
  handle.removeEventListener("pointercancel", endTimelineDrag);
  if (_tlDrag.readout) _tlDrag.readout.remove();
  _tlDrag = null;
  // 端點拖曳一律重算；整段平移只有真的移動過才重算（純點擊 = 只跳播放頭，交給 click）。
  // 全量同步：卡片時間欄 / ruler / caption / 未儲存徽章 / timeline 重建
  if (edge !== "move" || moved) rerenderEditState();
}

export {
  TL_ZOOM_MIN,
  TL_ZOOM_MAX,
  TL_ZOOM_STEP,
  drawTlWaveform,
  renderCardTimeline,
  renderPodcastCardTrack,
  selectTitleCard,
  setCardTrackVisible,
  setTlZoom,
  syncTimelineBlock,
  updateTimelinePlayhead,
};
