/* 影片模式 · 時間軸線性剪輯 —— 丟棄式可點原型
 *
 * 目的：讓使用者本人點過、確認「在時間軸框選一段標記剪除」的互動模型與版面。
 * 硬邊界（見 docs/plans/2026-08-24-video-mode-timeline-trim.md Phase 1）：
 *   · 剪除只是「前端視覺狀態」，不呼叫 /api/save、不寫任何檔。
 *   · 剪除 state 用未來真功能的形狀：cuts = [[start,end], ...]（秒），方便日後接真功能。
 *   · 不改 app.js / 後端；配色間距一律沿用 tokens.css 變數。
 *
 * 雙載入模式：
 *   預設    → 真 /api/waveform（目前開啟集）＋ <video src="/api/video">，需要真 app server。
 *   ?demo=1 → bundled sample-waveform.json ＋ sample-video.mp4，不需 server/開集即可整頁渲染。
 */
import {
  applyTimelineZoom,
  positionPlayhead,
  drawWaveformCore,
  assignCardLanes,
  renderCardTrackCore,
  bindCardTimeDragCore,
  hexToAss,
  buildSubtitleCss,
  subtitlePositionCss,
} from "./timeline-core.js";

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const DEMO = new URLSearchParams(location.search).has("demo");

  // ── 全域 state（剪除模型與未來真功能同形狀）─────────────────────────
  const state = {
    duration: 0, // 原長（秒）
    waveform: null, // { peaks, peak_max, bucket_ms, silences, duration }
    cuts: [], // [[start,end], ...] 秒，order-preserving、已合併重疊
    tlZoom: 1, // 時間軸縮放倍率
    undoStack: [], // 每次變更前推入 cuts 深拷貝
    selecting: null, // 拖曳選區中：{ a, b }（秒）
    // 鍵盤標的進出點：{ in, out }（秒，任一可為 null）。與 selecting 分開，
    // 因為它在放開滑鼠後要一直留著 —— 標了進點還能慢慢挪播放頭再標出點。
    mark: { in: null, out: null },
    skipPreview: true, // 播放時是否跳過剪除段
    subs: [], // 字幕卡：[{ start, end, text }]（demo 用 sample-subtitles.json）
    editingSub: null, // 正在就地編輯的字幕句 index（null=無）
    // 標題卡：畫面上疊的視覺卡片，與逐句字幕是兩回事
    // [{ id, start, end, tpl, text, scale, x, y }]；x/y 是 0–1 相對座標（卡片中心）
    titleCards: [],
    selectedCard: null, // 目前選取的標題卡 id
    editingCard: null, // 正在就地編輯文字的標題卡 id
    // 樣式記憶：新卡沿用上一張的版型與大小；位置一律回該版型的固定預設位，
    // 拖過的位置只屬於那張卡（使用者要「每個字卡有固定預設位、但可自己移動」）
    lastCard: { tpl: "big", scale: 100 },
    // 字幕樣式：對應後端 defaults.yaml subtitle_style 的 9 個參數
    // 顏色在前端用 hex 存（color picker 值），顯示時轉回 ASS &H00BBGGRR
    style: {
      font_name: "Hiragino Sans GB",
      font_size: 60,
      bold: 1,
      primary_colour_hex: "#ffffff", // &H00FFFFFF
      outline_colour_hex: "#000000", // &H00000000
      border_style: 1, // 1=描邊+陰影、3=不透明底色塊
      outline: 2,
      shadow: 1,
      alignment: 2, // SSA v3：2=底部置中、10=畫面正中、6=頂部置中
      margin_v: 100,
    },
    // ── 影片／聲音／字幕對齊（與這集 episode.yaml 共用；真模式才有值）─────
    // 五個純量欄位直接 round-trip（GET /api/episode 讀、POST /api/save 寫）。
    // 位移數學只作用在「字幕卡顯示時間」上，這五個純量本身不套任何 shift。
    audioPath: "", // audio.path：外接音檔相對路徑（有值才代表用外接音軌）
    audioSyncOffset: 0, // audio.sync_offset：外接音檔相對影片偏移（秒）
    camSyncOffsetB: 0, // camera_sync_offset.b：cam B 相對 cam A（秒）
    headTrimSec: 0, // head_trim_sec：片頭裁切（cam A 軸，秒，RAW）
    tailTrimSec: 0, // tail_trim_sec：片尾裁切（cam A 軸，秒，RAW）
    subtitleOffsetSec: 0, // subtitle_offset_sec：非破壞性字幕偏移（秒）
    episodeDir: "", // 目前集路徑；存檔帶回讓後端確認目標集（多分頁防呆）
    align: { state: "idle", err: "" }, // 對齊面板四態：idle/loading/error/success（demo→demo）
  };
  window.__vt = state; // 供 CDP 走查讀取（丟棄式原型，不影響行為）

  // ── 通用工具 ────────────────────────────────────────────────────
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  // 秒 → mm:ss.s
  function fmt(sec) {
    sec = Math.max(0, sec || 0);
    const m = Math.floor(sec / 60);
    const s = sec - m * 60;
    return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
  }

  // 剪除總長 Σ(end-start)
  function cutTotal() {
    return state.cuts.reduce((a, [s, e]) => a + (e - s), 0);
  }
  // 剪後總長 = 原長 − Σ剪除
  function finalDuration() {
    return Math.max(0, state.duration - cutTotal());
  }

  // 從 tokens.css 讀色（不寫死色碼）——canvas 需要實際色字串
  function token(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
  }

  // ── 剪除模型操作 ────────────────────────────────────────────────
  // 復原點是「整個編輯狀態」的快照 —— 只存剪除的話，斷完句按 ⌘Z
  // 會把不相干的剪除吐回來，句子卻還在（使用者按下去只會更亂）。
  function snapshot() {
    return {
      cuts: state.cuts.map((c) => c.slice()),
      subs: state.subs.map((x) => ({
        start: x.start,
        end: x.end,
        text: x.text,
      })),
      cards: state.titleCards.map((c) => Object.assign({}, c)),
      lastCard: Object.assign({}, state.lastCard),
      selectedCard: state.selectedCard,
      // 樣式也進快照：匯入一份剪輯指令會連字幕樣式一起換掉，
      // 復原若只還原剪除與卡片，畫面會停在別人的樣式上。
      style: Object.assign({}, state.style),
    };
  }
  // tag 相同的連續操作合併成一個復原點（連打 20 個字不該按 20 次 ⌘Z）；
  // 一段操作結束時呼叫 endUndoGroup() 封口，下一段才會另開復原點。
  let lastUndoTag = null;
  function pushUndo(tag) {
    if (tag && tag === lastUndoTag) return;
    lastUndoTag = tag || null;
    state.undoStack.push(snapshot());
    if (state.undoStack.length > 100) state.undoStack.shift();
  }
  function endUndoGroup() {
    lastUndoTag = null;
  }

  // 加入一段剪除並「合併重疊/相鄰」→ 維持 canonical、不會相鄰（跳播才不會卡住）
  // ── 鍵盤剪輯流 ───────────────────────────────────────────────
  // 滑鼠拖選適合抓大概，抓不準句子的頭尾；鍵盤這條路是給「聽到雜音就地剪掉」用的。
  const NUDGE_SMALL = 0.1; // ← →
  const NUDGE_BIG = 1; // ⇧ + ← →

  // 打字中的欄位：這些地方的單鍵都是輸入，不是快捷鍵
  function isTypingTarget(t) {
    if (!t || !t.tagName) return false;
    const tag = t.tagName;
    return (
      tag === "INPUT" ||
      tag === "TEXTAREA" ||
      tag === "SELECT" ||
      t.isContentEditable === true
    );
  }

  // 標進點／出點。標下去若讓範圍反過來（出點跑到進點前面），
  // 就把另一端清掉重新開始 —— 硬留一個負範圍只會讓人以為壞了。
  function setMark(which) {
    const v = $("vt-video");
    if (!v) return;
    const t = clamp(v.currentTime, 0, state.duration);
    const m = state.mark;
    if (which === "in") {
      m.in = t;
      if (m.out != null && m.out <= t) m.out = null;
    } else {
      m.out = t;
      if (m.in != null && m.in >= t) m.in = null;
    }
    renderSelection();
    const r = markRange();
    toast(which === "in" ? `進點 ${fmt(t)}` : `出點 ${fmt(t)}`);
    return r;
  }

  function clearMark() {
    state.mark.in = null;
    state.mark.out = null;
    renderSelection();
  }

  // 把進出點圈的範圍剪掉。做不到就講原因，不要靜靜地沒反應 ——
  // 鍵盤操作看不到滑鼠軌跡，沒有回饋就等於壞掉。
  function cutMarked() {
    const r = markRange();
    if (!r) {
      toast("先按 I 標進點、O 標出點，再按 ⌫ 剪除");
      return false;
    }
    if (!addCut(r.a, r.b)) {
      toast(`範圍不到 ${MIN_CUT} 秒，太短了不會剪`);
      return false;
    }
    const len = r.b - r.a;
    clearMark();
    render();
    toast(`已剪除 ${fmt(r.a)}–${fmt(r.b)}（${len.toFixed(1)} 秒）`);
    return true;
  }

  function addCut(a, b) {
    const lo = clamp(Math.min(a, b), 0, state.duration);
    const hi = clamp(Math.max(a, b), 0, state.duration);
    if (hi - lo < MIN_CUT) return false; // 太短當誤觸
    pushUndo();
    const merged = [...state.cuts, [lo, hi]].sort((x, y) => x[0] - y[0]);
    const out = [merged[0].slice()];
    for (let i = 1; i < merged.length; i++) {
      const cur = merged[i];
      const last = out[out.length - 1];
      if (cur[0] <= last[1])
        last[1] = Math.max(last[1], cur[1]); // 重疊/相鄰 → 併
      else out.push(cur.slice());
    }
    state.cuts = out;
    return true;
  }

  function removeCutAt(index) {
    if (index < 0 || index >= state.cuts.length) return;
    pushUndo();
    state.cuts.splice(index, 1);
  }

  function undo() {
    if (!state.undoStack.length) return;
    const s = state.undoStack.pop();
    lastUndoTag = null;
    state.cuts = s.cuts;
    state.subs = s.subs;
    state.titleCards = s.cards;
    state.lastCard = s.lastCard;
    // 復原後那張卡可能已經不存在了，選取要跟著失效
    state.selectedCard = s.cards.some((c) => c.id === s.selectedCard)
      ? s.selectedCard
      : null;
    state.editingSub = null;
    state.editingCard = null;
    if (s.style) {
      Object.assign(state.style, s.style);
      applyStyle(); // 連同把樣式面板的控制項回填
    }
    render();
    renderLines(); // 句數變了，右欄要整份重建（列的閉包索引會失效）
    renderCardTrack();
  }

  const MIN_CUT = 0.15; // 秒；短於此的拖曳視為點擊定位而非剪除

  // ── 波形繪製（T1：core 抽到 timeline-core.js，與 podcast 模式共用）───
  const TL_WAVE_MAX_PX = 16384;
  const GUIDE_MIN_ZOOM = 2;
  const GUIDE_MIN_GAP_PX = 5;
  function drawWaveform() {
    const tl = $("vt-timeline");
    const canvas = $("vt-waveform");
    const wf = state.waveform;
    if (!tl || !canvas || !wf || !wf.peaks || !wf.peaks.length) return;
    // 影片模式沒有窗口化時間軸，t0 恆為 0（整片）
    drawWaveformCore({
      tl,
      canvas,
      wf,
      t0: 0,
      total: state.duration,
      maxPx: TL_WAVE_MAX_PX,
      accentColor: token("--accent"),
      silence: {
        zoom: state.tlZoom,
        minZoom: GUIDE_MIN_ZOOM,
        minGapPx: GUIDE_MIN_GAP_PX,
        dimColor: token("--text-dim"),
      },
    });
  }

  // ── 剪除段 / 選區 DOM 渲染 ──────────────────────────────────────
  function pct(t) {
    return (t / (state.duration || 1)) * 100;
  }

  // 剪除段的邊界微調：拖左右兩端改進出點。剪多剪少是常態，
  // 只能「整段取消再重畫」等於每次都要重來一遍。
  // 夾制規則：不短於 MIN_CUT，且與鄰段之間至少留 MIN_CUT 的保留片段
  // —— 兩段剪除黏在一起會破壞 cuts 的 canonical 形狀（跳播會卡）。
  function bindCutTrim(el, index, side) {
    el.addEventListener("pointerdown", (e) => {
      if (e.button != null && e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const tl = $("vt-timeline");
      const rect = tl.getBoundingClientRect();
      const x0 = e.clientX;
      const [s0, e0] = state.cuts[index];
      const prev = state.cuts[index - 1];
      const next = state.cuts[index + 1];
      const loBound = prev ? prev[1] + MIN_CUT : 0;
      const hiBound = next ? next[0] - MIN_CUT : state.duration;
      el.classList.add("is-trimming");
      const move = (ev) => {
        pushUndo("cuttrim:" + index); // 整段拖曳只留一個復原點
        const dt = ((ev.clientX - x0) / rect.width) * state.duration;
        const cut = state.cuts[index];
        if (side === "l") cut[0] = clamp(s0 + dt, loBound, e0 - MIN_CUT);
        else cut[1] = clamp(e0 + dt, s0 + MIN_CUT, hiBound);
        const box = el.closest(".vt-cut");
        box.style.left = `${pct(cut[0])}%`;
        box.style.width = `${pct(cut[1] - cut[0])}%`;
        box.querySelector(".vt-cut-label").textContent =
          `✕ ${(cut[1] - cut[0]).toFixed(1)}s`;
        renderStats(); // 剪後總長要跟著手走，不然拖到一半不知道剪掉多少
      };
      const up = () => {
        el.classList.remove("is-trimming");
        endUndoGroup();
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        render(); // 字幕淡化／跳播區間都跟著邊界變，收手時整輪重算
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }

  function renderCuts() {
    const tl = $("vt-timeline");
    // 清掉舊的剪除段（保留 canvas / 播放頭 / 選區）
    tl.querySelectorAll(".vt-cut").forEach((n) => n.remove());
    state.cuts.forEach(([s, e], i) => {
      const el = document.createElement("div");
      el.className = "vt-cut";
      el.style.left = `${pct(s)}%`;
      el.style.width = `${pct(e - s)}%`;
      el.title = `剪除 ${fmt(s)}–${fmt(e)}（長 ${(e - s).toFixed(1)} 秒）\n點=取消這段、拖兩端=改剪除範圍`;
      const label = document.createElement("span");
      label.className = "vt-cut-label";
      label.textContent = `✕ ${(e - s).toFixed(1)}s`;
      el.appendChild(label);
      // 點剪除段 = 取消該段；但拖把手放開時 click 會冒泡到這裡，要擋掉
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (ev.target.closest(".vt-cut-h")) return;
        removeCutAt(i);
        render();
      });
      // 太窄時兩個把手會把整段佔滿，連點取消都按不到 —— 那就先不畫，
      // 要微調可以放大時間軸再拖（跟標題卡同一套規矩）
      const wpx = ((e - s) / (state.duration || 1)) * tl.clientWidth;
      if (wpx >= 26)
        ["l", "r"].forEach((side) => {
          const h = document.createElement("div");
          h.className = "vt-cut-h is-" + side;
          h.title = side === "l" ? "拖曳改剪除起點" : "拖曳改剪除終點";
          el.appendChild(h);
          bindCutTrim(h, i, side);
        });
      tl.appendChild(el);
    });
  }

  // 進出點目前圈出來的範圍。只標了一邊時，另一邊跟著播放頭走 ——
  // 這樣按下 I 之後拖播放頭就看得到範圍長大，不必先湊齊兩點才有回饋。
  function markRange() {
    const m = state.mark;
    if (m.in == null && m.out == null) return null;
    const v = $("vt-video");
    const now = v ? clamp(v.currentTime, 0, state.duration) : 0;
    const a = m.in == null ? now : m.in;
    const b = m.out == null ? now : m.out;
    return b - a > 1e-6 ? { a, b } : null;
  }

  function renderSelection() {
    const tl = $("vt-timeline");
    let sel = tl.querySelector(".vt-selection");
    // 拖曳中的臨時選區優先；沒在拖就顯示鍵盤 I/O 標出來的範圍
    let r = null;
    let isMark = false;
    if (state.selecting) {
      const { a, b } = state.selecting;
      r = { a: Math.min(a, b), b: Math.max(a, b) };
    } else {
      r = markRange();
      isMark = !!r;
    }
    if (!r) {
      if (sel) sel.remove();
      return;
    }
    if (!sel) {
      sel = document.createElement("div");
      sel.className = "vt-selection";
      tl.appendChild(sel);
    }
    sel.classList.toggle("is-mark", isMark);
    // 鍵盤標的範圍會停在畫面上，直接把「按什麼鍵會怎樣」寫在上面
    sel.textContent = isMark ? `⌫ 剪除 ${(r.b - r.a).toFixed(1)}s` : "";
    sel.style.left = `${pct(r.a)}%`;
    sel.style.width = `${pct(r.b - r.a)}%`;
  }

  function renderStats() {
    $("vt-stat-orig").textContent = fmt(state.duration);
    $("vt-stat-final").textContent = fmt(finalDuration());
  }

  // 全量重繪（波形只在縮放/resize/載入時另外呼叫，這裡不重畫波形避免播放卡頓）
  function render() {
    renderCuts();
    renderSelection();
    renderStats();
    renderSubs();
    syncCardInspector();
    updateCardLayer(true);
    updatePlayhead();
  }

  // ── 字幕軌 ──────────────────────────────────────────────────────
  // head_trim / tail_trim 不在 state.cuts 裡，但後端 segment_plan.removed_with_trim
  // 會把 [0,head]、[dur-tail,dur] 一起併進「被移除區間」丟卡/丟字幕。前端據此讓時間軸
  // 變灰、出片前攔截，跟後端一致（後端 drop 行為不改）。
  function trimIntervals() {
    const out = [];
    const dur = state.duration || 0;
    const h = state.headTrimSec || 0;
    if (h > 0) out.push([0, dur > 0 ? Math.min(h, dur) : h]);
    const t = state.tailTrimSec || 0;
    if (t > 0 && dur > 0) out.push([Math.max(0, dur - t), dur]);
    return out;
  }
  // state.cuts ∪ 片頭/片尾裁切，合併成不重疊、依起點排序的區間。
  // 內層區間先 clone —— 合併會就地改端點，不能動到 state.cuts 的原陣列。
  function removedIntervals() {
    const all = state.cuts
      .concat(trimIntervals())
      .map(([s, e]) => [s, e])
      .sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const [s, e] of all) {
      const last = merged[merged.length - 1];
      if (last && s <= last[1]) last[1] = Math.max(last[1], e);
      else merged.push([s, e]);
    }
    return merged;
  }
  // 卡片能否出現在成品，後端只看「起點落不落在被移除區間」（title_cards，二元，
  // 跟字幕的部分覆蓋不同）。起點被移除＝整張不出現。
  function cardDropped(c) {
    return removedIntervals().some(([s, e]) => s <= c.start && c.start < e);
  }
  // 一張字卡對剪除的關係：kept（完全保留）/ cut（完全落在剪除區）/ partial（跨邊界）
  function subStatus(sub) {
    let covered = 0;
    let overlap = false;
    for (const [cs, ce] of removedIntervals()) {
      const lo = Math.max(sub.start, cs);
      const hi = Math.min(sub.end, ce);
      if (hi > lo) {
        covered += hi - lo;
        overlap = true;
      }
    }
    if (!overlap) return "kept";
    return covered >= sub.end - sub.start - 1e-3 ? "cut" : "partial";
  }

  // 從 [a,b] 減掉所有剪除區間 → 剩下的存活片段（可能 0~多段）
  function subtractCuts(a, b) {
    let pieces = [[a, b]];
    for (const [cs, ce] of state.cuts) {
      const next = [];
      for (const [ps, pe] of pieces) {
        if (ce <= ps || cs >= pe) {
          next.push([ps, pe]); // 無交集
          continue;
        }
        if (cs > ps) next.push([ps, Math.min(cs, pe)]);
        if (ce < pe) next.push([Math.max(ce, ps), pe]);
      }
      pieces = next.filter(([x, y]) => y - x > 1e-4);
    }
    return pieces;
  }

  // 原時間 t → 剪後時間（往前補位：扣掉 t 之前所有剪除長度）
  function toFinalTime(t) {
    let shift = 0;
    for (const [cs, ce] of state.cuts) {
      if (ce <= t) shift += ce - cs;
      else if (cs < t) shift += t - cs; // t 落在剪除區內：夾到區間起點
    }
    return Math.max(0, t - shift);
  }

  const MIN_SUB_DUR = 0.3; // 秒；字幕拖曳不讓它縮到這以下（跟標題卡同一條底線）

  // 在時間軸上直接調某句字幕的進出點：整塊拖＝平移出現時間、拖左右兩端＝改起訖。
  // 跟標題卡同一套手感（不吸附、拖到哪算哪），但多一條字幕專屬規矩：
  // 不准跨過鄰句 —— 夾在前一句結束與後一句開始之間，字幕永遠不重疊也不換序。
  function bindSubTimeDrag(el, i, mode) {
    el.addEventListener("pointerdown", (e) => {
      if (e.button != null && e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const sub = state.subs[i];
      if (!sub) return;
      const rect = $("vt-sub-track").getBoundingClientRect();
      const x0 = e.clientX;
      const s0 = sub.start;
      const e0 = sub.end;
      const dur = e0 - s0;
      // 鄰句邊界：拖到哪都不准壓過去（維持不重疊、不換序的字幕不變式）
      const loBound = i > 0 ? state.subs[i - 1].end : 0;
      const hiBound =
        i < state.subs.length - 1 ? state.subs[i + 1].start : state.duration;
      const block = el.closest(".vt-sub") || el;
      block.__vtMoved = false;
      block.classList.add("is-dragging");
      const move = (ev) => {
        if (Math.abs(ev.clientX - x0) > 4) block.__vtMoved = true;
        pushUndo("subtime:" + i); // 整段拖曳只留一個復原點
        const dt = ((ev.clientX - x0) / rect.width) * state.duration;
        if (mode === "move") {
          // 平移：長度不變，整塊夾在鄰句之間
          const hi = Math.max(loBound, hiBound - dur);
          sub.start = clamp(s0 + dt, loBound, hi);
          sub.end = sub.start + dur;
        } else if (mode === "l") {
          sub.start = clamp(s0 + dt, loBound, e0 - MIN_SUB_DUR);
          sub.end = e0;
        } else {
          sub.start = s0;
          sub.end = clamp(e0 + dt, s0 + MIN_SUB_DUR, hiBound);
        }
        block.style.left = `${pct(sub.start)}%`;
        block.style.width = `${pct(sub.end - sub.start)}%`;
        block.title = `${fmt(sub.start)}–${fmt(sub.end)}　${sub.text}（拖=移動、拖兩端=改起訖）`;
      };
      const up = () => {
        block.classList.remove("is-dragging");
        endUndoGroup();
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        // 時間變了：時間軸塊、右欄時間標籤、影片上字幕預覽全部重算
        renderSubs();
        updateSubPreview();
        syncPlayingLine(false);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }

  function renderSubTrack() {
    const track = $("vt-sub-track");
    if (!track) return;
    track.querySelectorAll(".vt-sub, .vt-sub-cutband").forEach((n) => {
      if (n.isConnected) n.remove();
    });
    // 剪除帶：讓剪除區塊也跨到字幕軌，強化「這幾張卡在剪除區」
    state.cuts.forEach(([s, e]) => {
      const band = document.createElement("div");
      band.className = "vt-sub-cutband";
      band.style.left = `${pct(s)}%`;
      band.style.width = `${pct(e - s)}%`;
      track.appendChild(band);
    });
    state.subs.forEach((sub, i) => {
      const status = subStatus(sub);
      const el = document.createElement("div");
      el.className =
        "vt-sub" +
        (status === "cut"
          ? " is-cut"
          : status === "partial"
            ? " is-partial"
            : "") +
        (state.editingSub === i ? " is-active" : "");
      el.style.left = `${pct(sub.start)}%`;
      el.style.width = `${pct(sub.end - sub.start)}%`;
      el.title = `${fmt(sub.start)}–${fmt(sub.end)}　${sub.text}（點=跳到右欄那句、拖=移動、拖兩端=改起訖）`;
      const txt = document.createElement("span");
      txt.className = "vt-sub-txt";
      txt.textContent = sub.text;
      el.appendChild(txt);
      if (status === "partial") {
        const flag = document.createElement("span");
        flag.className = "vt-sub-flag";
        flag.textContent = "部分剪除";
        el.appendChild(flag);
      }
      // 點時間軸＝我要看這句：播放頭跟著過去，右欄游標同步就位。
      // 但剛剛是「拖」而不是「點」的話就別跳轉（避免拖完又莫名搶焦點）。
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (el.__vtMoved) {
          el.__vtMoved = false;
          return;
        }
        seekTo(sub.start);
        focusLine(i);
      });
      // 兩端把手改起訖、其餘拖整塊平移（比照標題卡；太窄不畫把手，放大再拖）
      const wpx =
        ((sub.end - sub.start) / (state.duration || 1)) * track.clientWidth;
      if (wpx >= 24)
        ["l", "r"].forEach((side) => {
          const h = document.createElement("div");
          h.className = "vt-sub-h is-" + side;
          h.title = side === "l" ? "拖曳改這句開始" : "拖曳改這句結束";
          el.appendChild(h);
          bindSubTimeDrag(h, i, side);
        });
      bindSubTimeDrag(el, i, "move");
      track.appendChild(el);
    });
  }

  // 重入保護：清空軌道時移除聚焦中的輸入框會同步觸發 blur，
  // blur 的 commit 又會呼叫 renderSubs —— 內層先重建完，外層再去 remove 已被搬走的節點就會炸。
  // 內層不重畫、只留旗標，等外層跑完再補一輪，state 一定反映得到。
  let subsRendering = false;
  let subsRenderAgain = false;
  function renderSubs() {
    if (subsRendering) {
      subsRenderAgain = true;
      return;
    }
    subsRendering = true;
    try {
      do {
        subsRenderAgain = false;
        renderSubTrack();
        renderCardTrack();
        refreshLineStates();
      } while (subsRenderAgain);
    } finally {
      subsRendering = false;
    }
  }

  // ── 標題卡（畫面上疊的視覺卡片，與逐句字幕分開）──────────────────
  // 三種版型：不給參數面板，只給大小與位置。字級以影片寬度為基準等比換算，
  // 所以同一張卡在不同視窗大小下看起來一樣。
  const CARD_TEMPLATES = {
    big: { name: "重點大字", base: 0.055, x: 0.5, y: 0.5 },
    lower: { name: "下標條", base: 0.034, x: 0.2, y: 0.84 },
    quote: { name: "引言框", base: 0.038, x: 0.5, y: 0.5 },
  };
  const CARD_DEFAULT_DUR = 3; // 拖版型上來的空白卡預設長度（秒）
  const MIN_CARD_DUR = 0.3; // 秒；再短就抓不到也讀不到，拖曳不讓它縮到這以下
  let cardSeq = 0;

  // 標題卡自動車道（lane-packing）與軌道渲染/拖曳幾何已搬進 timeline-core.js
  // 的 assignCardLanes/renderCardTrackCore/bindCardTimeDragCore（T4，podcast
  // 模式的 #tl-card-track 共用同一份）。零資料結構改動：車道只在畫面上算，
  // 不寫回 state.titleCards，每次 render 都重算。

  function makeCard(start, end, text, tpl, src) {
    const last = state.lastCard;
    const key = tpl || last.tpl;
    return {
      id: ++cardSeq,
      start: clamp(start, 0, state.duration),
      end: clamp(end, 0, state.duration),
      tpl: key,
      text: text || "",
      // 大小沿用上一張；位置一律落在該版型的固定預設位，之後可自己拖
      scale: last.scale,
      x: CARD_TEMPLATES[key].x,
      y: CARD_TEMPLATES[key].y,
      moved: false, // 使用者拖過沒有；拖過的位置換版型時不該被洗掉
      // 從哪一句升格來的（記那句「當時」的時間封套）。這是血緣，不是位置：
      // 卡被拖長拖短之後還認得自己的娘家，右欄的「已升格」標記與
      // 「這句字幕不再重複燒」才不會因為時間差了 0.1 秒就整個失效。
      src: src ? { start: src.start, end: src.end } : null,
    };
  }

  function rememberCard(c) {
    state.lastCard = { tpl: c.tpl, scale: c.scale };
  }

  function addCard(card) {
    state.titleCards.push(card);
    state.titleCards.sort((a, b) => a.start - b.start);
    // 新卡一律走 selectCard —— 它會把播放頭帶進這張卡的區間。
    // 少了這一步，剛升格的卡在畫面上根本看不到（面板卻亮著），
    // 使用者只會覺得「按了沒反應」。
    selectCard(card.id);
    return card;
  }

  // 這句是否已經升格成標題卡。
  // 先認血緣（卡記得自己從哪句來），認不到才退回「時間完全對齊」——
  // 後者是給沒有血緣的卡用的（匯入的指令、直接拖版型上來的空白卡）。
  // 早期只有時間對齊這一條，卡被拖長一點點就配不回原句，於是標題卡
  // 和字幕會同時燒出兩行一模一樣的字。
  function subMatchesCardSrc(c, sub) {
    return (
      !!c.src &&
      Math.abs(c.src.start - sub.start) < 1e-6 &&
      Math.abs(c.src.end - sub.end) < 1e-6
    );
  }
  function cardFromSub(sub) {
    if (!sub) return null;
    return (
      state.titleCards.find((c) => subMatchesCardSrc(c, sub)) ||
      state.titleCards.find(
        (c) =>
          !c.src &&
          Math.abs(c.start - sub.start) < 1e-6 &&
          Math.abs(c.end - sub.end) < 1e-6,
      ) ||
      null
    );
  }

  // 這句字幕是不是「已經被卡演掉了、不該再燒一次」。
  // 血緣（cardFromSub）只說得出「這句做過卡」，說不出「那張卡還在不在這個
  // 時段」—— 卡被拖到別處之後，這句在原時段又變回沒人講的狀態，字幕就該
  // 自己回來。所以取代與否再多一個條件：卡與這句的時間要真的有交集。
  //
  // 判準是「整句」而不是「逐幀」：輸出的字幕是一整句一個 cue，燒或不燒沒有
  // 半句的選項。預覽若逐幀判、輸出卻整句判，畫面就不等於成品 —— 那是這個
  // 原型最不能破的規矩。所以卡只要蓋到這句的任何一段，整句都讓位。
  function subCoveredByCard(sub) {
    const c = cardFromSub(sub);
    if (!c) return false;
    return Math.min(c.end, sub.end) - Math.max(c.start, sub.start) > 1e-6;
  }

  // 從字幕句一鍵升格：文字與時間直接沿用，不用打字也不用對時間
  function addCardFromSub(i) {
    const s = state.subs[i];
    if (!s) return null;
    // 同一句重按不該疊出一堆完全重疊的卡：已經做過就選取既有那張，
    // 讓使用者看到「這句已經有卡了」而不是靜靜長出第三張。
    const exist = cardFromSub(s);
    if (exist) {
      selectCard(exist.id);
      return exist;
    }
    pushUndo();
    return addCard(makeCard(s.start, s.end, s.text, null, s));
  }

  function findCard(id) {
    return state.titleCards.find((c) => c.id === id) || null;
  }

  function removeCard(id) {
    const i = state.titleCards.findIndex((c) => c.id === id);
    if (i < 0) return;
    pushUndo();
    state.titleCards.splice(i, 1);
    if (state.selectedCard === id) state.selectedCard = null;
    if (state.editingCard === id) state.editingCard = null;
    renderCardTrack();
    syncCardInspector();
    updateCardLayer(true);
  }

  function selectCard(id) {
    let seeked = false;
    state.selectedCard = id;
    const c = findCard(id);
    if (c) {
      rememberCard(c);
      // 卡只在自己的區間裡出現，所以選它就把播放頭帶進去 ——
      // 否則面板亮著、畫面上卻什麼都看不到，位置與大小根本沒得調。
      const v = $("vt-video");
      if (v && !(v.currentTime >= c.start && v.currentTime < c.end)) {
        v.currentTime = clamp(
          Math.min(c.start + 0.05, Math.max(c.start, c.end - 0.01)),
          0,
          state.duration,
        );
        seeked = true;
      }
    }
    renderCardTrack();
    syncCardInspector();
    updateCardLayer(true);
    if (seeked) {
      updatePlayhead();
      updateTimeReadout();
      updateSubPreview();
      syncPlayingLine(false);
    }
  }

  // ── 標題卡軌 ────────────────────────────────────────────────────
  function cardTitle(c) {
    const st = cardDropped(c) ? "cut" : subStatus(c);
    return (
      `${CARD_TEMPLATES[c.tpl].name}　${fmt(c.start)}–${fmt(c.end)}（長 ${(c.end - c.start).toFixed(1)} 秒）\n` +
      `${c.text || "（空白標題卡）"}\n` +
      (st === "cut"
        ? "這段被剪掉了，這張卡不會出現在成品裡\n"
        : st === "partial"
          ? "有一段被剪掉，這張卡在成品裡會變短\n"
          : "") +
      "點=選取、雙擊=改字、拖=移動、拖兩端=改長度、Delete=刪除"
    );
  }

  // 在時間軸上直接調卡的進出點：整塊拖＝平移（長度不變），拖左右兩端＝改長度。
  // 跟影片上拖位置同一套規矩 —— 不吸附，拖到哪就是哪；只夾在 0–總長內、不短於 MIN_CARD_DUR。
  // T4：幾何與事件本體搬進 timeline-core.js 的 bindCardTimeDragCore（t0=0、
  // total=state.duration，換算出來的數字與舊版逐位元相同）；本函式只負責注入
  // video 特有的 callback（真正的 undo 堆疊、selectCard、渲染收尾）。
  function bindCardTimeDrag(el, c, mode) {
    bindCardTimeDragCore(el, c, mode, {
      trackEl: $("vt-card-track"),
      t0: 0,
      total: state.duration,
      minDur: MIN_CARD_DUR,
      getTitle: cardTitle,
      onPointerDown: (cc) => selectCard(cc.id),
      onDragTick: (cc) => pushUndo("cardtime:" + cc.id), // 整段拖曳只留一個復原點
      onDragEnd: () => {
        endUndoGroup();
        renderCardTrack();
        syncCardInspector();
        // 卡的區間變了，播放頭現在可能剛好落在區間外／內，疊層要重算
        updateCardLayer(true);
      },
    });
  }

  // T4：軌道靜態渲染搬進 timeline-core.js 的 renderCardTrackCore（含
  // assignCardLanes 自動分軌），本函式只負責注入 video 特有的狀態
  // （cut/partial、雙擊改字、渲染完的右欄標記重算）。
  function renderCardTrack() {
    const track = $("vt-card-track");
    if (!track) return;
    renderCardTrackCore({
      track,
      cards: state.titleCards,
      t0: 0,
      total: state.duration,
      selectedId: state.selectedCard,
      editingId: state.editingCard,
      // 卡落在剪除區時要在軌上看得出來，不能等按下輸出才被 confirm 告知
      // （比照字幕塊）。片頭/片尾裁切區也算落區：起點被移除＝整張不出現，
      // 直接標 cut（不用等 confirm）。
      getStatus: (c) => (cardDropped(c) ? "cut" : subStatus(c)),
      getBadgeText: (c) => CARD_TEMPLATES[c.tpl].name.slice(0, 2),
      getCardText: (c) => c.text || "（空白標題卡）",
      getTitle: cardTitle,
      onSelect: (id) => selectCard(id),
      onDblClick: (id) => startEditCard(id),
      bindDrag: bindCardTimeDrag,
      renderEditingCard: renderCardInput,
      onAfterRender: () => {
        // 卡一變動，右欄的「已升格」標記與底部字幕預覽都要跟著重算
        syncLineCardMarks();
        updateSubPreview();
      },
    });
  }

  function startEditCard(id) {
    state.editingCard = id;
    state.selectedCard = id;
    renderCardTrack();
    const inp = $("vt-card-track").querySelector(".vt-sub-input");
    if (inp) {
      inp.focus();
      inp.select();
    }
  }

  function renderCardInput(track, c, top, height) {
    const inp = document.createElement("input");
    inp.className = "vt-sub-input";
    inp.type = "text";
    inp.value = c.text;
    inp.placeholder = "標題卡文字";
    inp.style.left = `${pct(c.start)}%`;
    // 跟卡片本體同一套車道位置，不然編輯中的卡永遠貼在第一列
    if (top != null) inp.style.top = `${top}px`;
    if (height != null) inp.style.height = `${height}px`;
    const commit = () => {
      if (state.editingCard !== c.id) return;
      c.text = inp.value;
      state.editingCard = null;
      renderCardTrack();
      updateCardLayer(true);
    };
    inp.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") {
        e.preventDefault();
        commit();
      } else if (e.key === "Escape") {
        state.editingCard = null;
        renderCardTrack();
      }
    });
    inp.addEventListener("blur", commit);
    inp.addEventListener("click", (e) => e.stopPropagation());
    track.appendChild(inp);
  }

  // ── 影片上的標題卡疊層 ──────────────────────────────────────────
  // 疊層對齊 video 的實際框（stage 可能比 video 寬），這樣 x/y 相對座標才準
  function syncCardLayerBox() {
    const v = $("vt-video");
    const layer = $("vt-card-layer");
    if (!v || !layer) return null;
    const w = v.offsetWidth || v.clientWidth;
    const h = v.offsetHeight || v.clientHeight;
    layer.style.left = `${v.offsetLeft}px`;
    layer.style.top = `${v.offsetTop}px`;
    layer.style.right = "auto";
    layer.style.bottom = "auto";
    layer.style.width = `${w}px`;
    layer.style.height = `${h}px`;
    return { w, h };
  }

  // 錨點隨 x/y 連續變化：x=0 貼左緣、0.5 置中、1 貼右緣（y 同理）。
  // 舊版用 0.35／0.65 門檻切三段，跨過門檻時參考點會瞬間換掉，
  // 卡在畫面上橫跳半個身寬 —— 那就是「拖起來很卡」的真正來源。
  // 換算後渲染出的左緣 px ＝ c.x × (容器寬 − 卡身寬)，恆在框內。
  function cardTransform(c) {
    return `translate(${(-c.x * 100).toFixed(3)}%, ${(-c.y * 100).toFixed(3)}%)`;
  }

  // 同一時間點命中的所有卡，依 state.titleCards 順序（後加的疊在上面）。
  // 卡的進出點可以自由拖，重疊是常態 —— 下標條配大字報本來就該同時出現。
  // 只畫最後一張的話，被蓋掉的那張會在畫面上靜默消失，而時間軸上還看得到它。
  function cardsActiveAt(t) {
    return state.titleCards.filter((c) => t >= c.start && t < c.end);
  }

  // force=true 時忽略「內容沒變就不重畫」的快取（新增/改樣式後要立刻反映）
  let lastLayerKey = "";
  function updateCardLayer(force) {
    const layer = $("vt-card-layer");
    if (!layer) return;
    const box = syncCardLayerBox();
    if (!box) return;
    const v = $("vt-video");
    const t = (v && v.currentTime) || 0;
    // 疊層一律照播放頭決定 —— 之前「選取中就強制顯示」會讓預覽說謊：
    // 播放頭明明在區間外，畫面上還掛著那張卡。改由 selectCard() 把播放頭
    // 帶進區間來保證「選了看得到」，畫面就永遠等於真實輸出。
    const active = cardsActiveAt(t);
    const key = active
      .map((c) =>
        [
          c.id,
          c.tpl,
          c.text,
          c.scale,
          c.x.toFixed(4),
          c.y.toFixed(4),
          c.id === state.selectedCard ? 1 : 0,
        ].join("|"),
      )
      .concat(`${box.w}x${box.h}`)
      .join("　");
    // 拖曳中一律不重建：innerHTML 清掉會把正在拖的那個節點換成新的，
    // 拖曳寫進去的位置就全數落在孤兒節點上，畫面看起來像卡住不動。
    if (draggingCardId !== null) return;
    if (!force && key === lastLayerKey) return;
    lastLayerKey = key;
    layer.innerHTML = "";
    active.forEach((c) => {
      const el = document.createElement("div");
      el.className =
        `vt-card-view tpl-${c.tpl}` +
        (c.id === state.selectedCard ? " is-selected" : "");
      el.dataset.id = String(c.id);
      el.textContent = c.text || "標題卡文字";
      el.style.fontSize = `${box.w * CARD_TEMPLATES[c.tpl].base * (c.scale / 100)}px`;
      el.style.left = `${c.x * 100}%`;
      el.style.top = `${c.y * 100}%`;
      el.style.transform = cardTransform(c);
      layer.appendChild(el);
      bindCardDrag(el, c, box);
    });
  }

  // 字幕列四顆小鈕的圖示（14×14 viewBox，線稿、吃 currentColor）
  const TOOL_ICONS = {
    // 切：一條被剪刀從中切斷的線
    split:
      '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"><path d="M7 1v4M7 9v4"/><circle cx="3.2" cy="7" r="1.7"/><circle cx="10.8" cy="7" r="1.7"/><path d="M4.9 7h4.2"/></svg>',
    // 併：兩條線收攏成一條，箭頭往上併入前一句
    merge:
      '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M2 11h10"/><path d="M7 8V2"/><path d="M4.4 4.6L7 2l2.6 2.6"/></svg>',
    // 卡：畫面框裡一條實心橫條（標題卡的樣子）
    card: '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"><rect x="1.4" y="2.6" width="11.2" height="8.8" rx="1.4"/><rect x="3.6" y="6.4" width="6.8" height="2.2" rx="0.6" fill="currentColor" stroke="none"/></svg>',
    // 刪：垃圾桶
    delete:
      '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3.6h10"/><path d="M5.4 3.6V2.2h3.2v1.4"/><path d="M3.4 3.6l.6 8h6l.6-8"/><path d="M6 6v3.6M8 6v3.6"/></svg>',
  };

  let draggingCardId = null;

  // 在影片上直接拖定位：拖到哪就停在哪，不做任何吸附
  function bindCardDrag(el0, c, box) {
    el0.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      selectCard(c.id); // 這一步可能重建整層，所以節點要在它之後才抓
      const layer = $("vt-card-layer");
      const el = layer.querySelector(`[data-id="${c.id}"]`) || el0;
      const rect = layer.getBoundingClientRect();
      // setPointerCapture 在某些環境（自動化、觸控筆切換）會丟 InvalidStateError，
      // 抓住就好；監聽掛在 window 上，拖出卡片外一樣收得到。
      try {
        el.setPointerCapture(e.pointerId);
      } catch (_) {}
      draggingCardId = c.id;
      el.classList.add("is-dragging");
      // 抓點偏移：按在卡的哪個位置就從哪裡帶著走，不讓卡跳到游標底下。
      const cr = el.getBoundingClientRect();
      const gx = e.clientX - cr.left,
        gy = e.clientY - cr.top;
      // 可移動距離是「容器 − 卡身」，因為錨點已改成連續換算
      const spanX = Math.max(1, rect.width - cr.width),
        spanY = Math.max(1, rect.height - cr.height);
      let raf = 0;
      const paint = () => {
        raf = 0;
        el.style.left = `${c.x * 100}%`;
        el.style.top = `${c.y * 100}%`;
        el.style.transform = cardTransform(c);
      };
      const move = (ev) => {
        pushUndo("drag:" + c.id); // 整段拖曳只留一個復原點
        c.moved = true;
        c.x = clamp((ev.clientX - gx - rect.left) / spanX, 0, 1);
        c.y = clamp((ev.clientY - gy - rect.top) / spanY, 0, 1);
        // 一幀內多個 pointermove 只寫一次樣式，省掉重複的版面計算
        if (!raf) raf = requestAnimationFrame(paint);
      };
      const up = () => {
        if (raf) {
          cancelAnimationFrame(raf);
          paint();
        }
        draggingCardId = null;
        el.classList.remove("is-dragging");
        endUndoGroup();
        rememberCard(c);
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        syncCardInspector();
        updateCardLayer(true);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }

  // ── 標題卡面板：只有大小與位置 ──────────────────────────────────
  function syncCardInspector() {
    const box = $("vt-card-inspector");
    if (!box) return;
    const c = findCard(state.selectedCard);
    box.hidden = !c;
    const hint = $("vt-tpl-hint");
    if (hint) hint.hidden = !!c;
    if (!c) return;
    $("ct-scale").value = String(c.scale);
    $("ct-scale-val").textContent = `${c.scale}%`;
    // 版型縮圖同步高亮，讓「這張卡是哪個版型」一眼看得出來
    document.querySelectorAll("#vt-tpl-grid .vt-tpl").forEach((t) => {
      t.classList.toggle("is-on", t.dataset.tpl === c.tpl);
    });
  }

  function bindCardPanel() {
    $("ct-scale").addEventListener("input", (e) => {
      const c = findCard(state.selectedCard);
      if (!c) return;
      pushUndo("scale:" + c.id);
      c.scale = +e.target.value;
      $("ct-scale-val").textContent = `${c.scale}%`;
      rememberCard(c);
      updateCardLayer(true);
    });
    $("ct-scale").addEventListener("change", endUndoGroup);
    // 拖歪了要回得去：這是刪掉九宮格後唯一的「固定預設位」出口
    $("ct-reset-pos").addEventListener("click", () => {
      const c = findCard(state.selectedCard);
      if (!c) return;
      pushUndo();
      c.x = CARD_TEMPLATES[c.tpl].x;
      c.y = CARD_TEMPLATES[c.tpl].y;
      c.moved = false; // 回到預設位＝把位置交還給版型，之後換版型會跟著走
      updateCardLayer(true);
    });
    // 版型縮圖：點＝把選取中的卡換版型；拖＝拖到軌道上長一張新卡
    document.querySelectorAll("#vt-tpl-grid .vt-tpl").forEach((t) => {
      t.addEventListener("click", () => {
        const tpl = t.dataset.tpl;
        const c = findCard(state.selectedCard);
        if (!c) {
          // 沒選卡時點版型＝在播放頭位置開一張新的。之前是靜靜地什麼都不做，
          // 使用者只會覺得「這個縮圖點不動」。
          const v = $("vt-video");
          const at = clamp(
            (v && v.currentTime) || 0,
            0,
            Math.max(0, state.duration - 0.5),
          );
          pushUndo();
          const nc = addCard(
            makeCard(
              at,
              Math.min(state.duration, at + CARD_DEFAULT_DUR),
              "",
              tpl,
            ),
          );
          startEditCard(nc.id);
          return;
        }
        pushUndo();
        c.tpl = tpl;
        // 只有沒被拖過的卡才吃版型預設位 —— 使用者親手擺過的位置不能被換版型洗掉
        if (!c.moved) {
          c.x = CARD_TEMPLATES[c.tpl].x;
          c.y = CARD_TEMPLATES[c.tpl].y;
        }
        rememberCard(c);
        renderCardTrack();
        syncCardInspector();
        updateCardLayer(true);
      });
      t.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/vt-tpl", t.dataset.tpl);
        e.dataTransfer.effectAllowed = "copy";
      });
    });
    const track = $("vt-card-track");
    track.addEventListener("dragover", (e) => {
      if (!e.dataTransfer.types.includes("text/vt-tpl")) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      track.classList.add("is-dropping");
    });
    track.addEventListener("dragleave", () =>
      track.classList.remove("is-dropping"),
    );
    track.addEventListener("drop", (e) => {
      const tpl = e.dataTransfer.getData("text/vt-tpl");
      track.classList.remove("is-dropping");
      if (!tpl || !CARD_TEMPLATES[tpl]) return;
      e.preventDefault();
      const rect = track.getBoundingClientRect();
      const t = clamp(
        ((e.clientX - rect.left) / rect.width) * state.duration,
        0,
        Math.max(0, state.duration - 0.5),
      );
      pushUndo();
      const c = addCard(
        makeCard(t, Math.min(state.duration, t + CARD_DEFAULT_DUR), "", tpl),
      );
      startEditCard(c.id);
    });
    // 點軌道空白處＝取消選取（讓預覽回到跟著播放頭走）
    track.addEventListener("click", () => {
      state.selectedCard = null;
      renderCardTrack();
      syncCardInspector();
      updateCardLayer(true);
    });
  }

  // ── 字幕句就地編輯（mock）───────────────────────────────────────
  // ── 字幕：改字／斷句／合併（沿用 podcast 剪輯的鍵盤語彙，UI 在右欄）──
  // Enter＝從游標處斷句、句首 Backspace＝併入前一句、Tab＝下一句、Esc＝離開。
  // 斷句後的時間分配與 app.js expandedCards / 後端 allocate_split_times 同一規則：
  // 每字 0.3 秒緊湊排；原句長度不夠就整段等比壓縮，不會超出原句封套。
  const SPLIT_SEC_PER_CHAR = 0.3;

  function startEditSub(i, caret) {
    state.editingSub = i;
    renderLines();
    renderSubTrack();
    focusLine(i, caret);
  }

  // 從 offset 把第 i 句切成兩句；回傳是否真的切了
  function splitSub(i, offset) {
    const sub = state.subs[i];
    if (!sub) return false;
    const left = sub.text.slice(0, offset);
    const right = sub.text.slice(offset);
    if (!left.trim() || !right.trim()) return false; // 頭尾切等於沒切
    pushUndo();
    const lens = [Math.max(left.length, 1), Math.max(right.length, 1)];
    const total = lens[0] + lens[1];
    const dur = sub.end - sub.start;
    const rate =
      total * SPLIT_SEC_PER_CHAR <= dur ? SPLIT_SEC_PER_CHAR : dur / total;
    const mid = sub.start + lens[0] * rate;
    state.subs.splice(
      i,
      1,
      { start: sub.start, end: mid, text: left },
      {
        start: mid,
        end: Math.min(sub.start + total * rate, sub.end),
        text: right,
      },
    );
    return true;
  }

  // 把第 i 句併進前一句：文字接起來，時間封套取「前句起 → 本句迄」
  function mergeSubIntoPrev(i) {
    if (i <= 0 || !state.subs[i]) return -1;
    pushUndo();
    const prev = state.subs[i - 1];
    const cur = state.subs[i];
    const joint = prev.text.length; // caret 落在交界處，跟 podcast 編輯器一致
    prev.text = prev.text + cur.text;
    prev.end = cur.end;
    state.subs.splice(i, 1);
    return joint;
  }

  // ── 右欄：字幕逐句清單（改字／斷句／合併都在這裡）──────────────────
  // 直式一句一列：時間戳｜文字｜工具鈕（滑到或編到那列才浮出）。
  // 打字不重建清單（游標才不會跳），只有句數變動才整份重畫。
  // 刪掉整句字幕。只移除這句字幕，不動剪除也不動總長 ——
  // 「這句不要字幕」跟「這段影片不要」是兩件事，混在一起會很難救。
  function deleteSub(i) {
    if (!state.subs[i]) return false;
    pushUndo();
    state.subs.splice(i, 1);
    state.editingSub = null;
    return true;
  }

  function focusLine(i, caret) {
    const row = $("vt-line-list").querySelector(`.vt-line[data-i="${i}"]`);
    if (!row) return;
    const inp = row.querySelector(".vt-line-input");
    inp.focus();
    if (caret == null) inp.select();
    else inp.setSelectionRange(caret, caret);
    row.scrollIntoView({ block: "nearest" });
  }

  // 只刷狀態（剪除造成的淡化／時間位移），不動 DOM 結構
  function refreshLineStates() {
    const list = $("vt-line-list");
    if (!list) return;
    list.querySelectorAll(".vt-line").forEach((row) => {
      const sub = state.subs[Number(row.dataset.i)];
      if (!sub) return;
      const status = subStatus(sub);
      row.classList.toggle("is-cut", status === "cut");
      row.classList.toggle("is-partial", status === "partial");
      row.querySelector(".vt-line-time").textContent = fmt(sub.start);
    });
  }

  // 重建期間舊輸入框會被 detach 並同步觸發 blur，那時它的 value 已經是過期世代
  // （例如剛斷完句，DOM 還是斷句前的整句）—— 寫回 state 就等於把斷句結果蓋掉。
  let linesRebuilding = false;
  function renderLines() {
    const list = $("vt-line-list");
    if (!list) return;
    linesRebuilding = true;
    list.innerHTML = "";
    state.subs.forEach((sub, i) => {
      const row = document.createElement("div");
      row.className = "vt-line";
      row.dataset.i = String(i);

      const time = document.createElement("span");
      time.className = "vt-line-time";
      time.textContent = fmt(sub.start);
      time.title = "跳到這句";
      time.addEventListener("click", () => {
        seekTo(sub.start);
      });
      row.appendChild(time);

      const inp = document.createElement("input");
      inp.className = "vt-line-input";
      inp.type = "text";
      inp.value = sub.text;
      row.appendChild(inp);

      const flush = () => {
        if (linesRebuilding || !inp.isConnected) return; // 過期節點的值不算數
        if (state.subs[i]) state.subs[i].text = inp.value;
      };
      const doSplit = () => {
        flush();
        const at = inp.selectionStart ?? inp.value.length;
        if (!splitSub(i, at)) return;
        startEditSub(i + 1, 0); // 游標落在新句開頭，可以接著往下斷
        updateSubPreview();
      };
      const doMerge = () => {
        if (i === 0) return;
        flush();
        const joint = mergeSubIntoPrev(i);
        if (joint < 0) return;
        startEditSub(i - 1, joint); // 游標停在接縫，接錯了馬上能改
        updateSubPreview();
      };

      const doDelete = () => {
        if (!deleteSub(i)) return;
        // 焦點接到原位（刪掉最後一句就落在新的最後一句），
        // 讓「連刪好幾句」不用每次重新用滑鼠點。
        const next = Math.min(i, state.subs.length - 1);
        if (next >= 0) startEditSub(next, 0);
        else {
          renderLines();
          renderSubTrack();
        }
        updateSubPreview();
      };

      // 打字即時進 state，只重畫時間軸那一塊的文字（清單不動，游標不跳）
      inp.addEventListener("input", () => {
        pushUndo("text:" + i); // 同一句連續打字合併成一個復原點
        flush();
        renderSubTrack();
        updateSubPreview();
      });
      inp.addEventListener("focus", () => {
        state.editingSub = i;
        endUndoGroup();
        // 字幕與標題卡是兩種東西，不該同時「選著」：開始改字就收掉卡面板，
        // 否則右欄上半還亮著上一張卡的大小滑桿，改到的卻是別的東西。
        if (state.selectedCard != null) {
          state.selectedCard = null;
          renderCardTrack();
          syncCardInspector();
          updateCardLayer(true);
        }
        renderSubTrack();
        updateSubPreview();
      });
      inp.addEventListener("blur", () => {
        flush();
        endUndoGroup();
        // 焦點還在清單裡（切到別句）就別收，避免高亮閃動
        setTimeout(() => {
          if (list.contains(document.activeElement)) return;
          if (state.editingSub !== i) return;
          state.editingSub = null;
          renderSubTrack();
        }, 0);
      });
      inp.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
          // 這裡 stopPropagation 擋掉了全域 ⌘Z。改字／刪句的復原點都在 state 裡，
          // 讓它走同一套 undo —— 否則剛按完「刪」（焦點正好落在輸入框）
          // 按 ⌘Z 會完全沒反應，而瀏覽器原生 undo 只還原輸入框的字、不還原 state。
          e.preventDefault();
          undo();
          return;
        }
        if (e.key === "Enter") {
          e.preventDefault();
          doSplit();
        } else if (e.key === "Backspace" && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          doDelete();
        } else if (
          e.key === "Backspace" &&
          inp.selectionStart === 0 &&
          inp.selectionEnd === 0 &&
          i > 0
        ) {
          e.preventDefault();
          doMerge();
        } else if (e.key === "Tab") {
          e.preventDefault();
          flush();
          const next = e.shiftKey ? i - 1 : i + 1;
          if (state.subs[next]) focusLine(next);
        } else if (e.key === "Escape") {
          inp.blur();
        }
      });

      // 小鈕與快捷鍵等價：滑鼠使用者也找得到斷句／合併／升格。
      // 圖示用 stroke: currentColor，hover／已升格／危險色沿用既有 class 規則；
      // 中文字改掛在 aria-label（讀螢幕與自動化都靠它認人）。
      const tools = document.createElement("div");
      tools.className = "vt-line-tools";
      const mkTool = (act, label, title, fn, disabled, cls) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "vt-line-tool" + (cls ? " " + cls : "");
        b.dataset.act = act;
        b.setAttribute("aria-label", label);
        b.innerHTML = TOOL_ICONS[act];
        b.title = title;
        b.disabled = !!disabled;
        // mousedown 先於 blur，preventDefault 才不會讓輸入框先收掉
        b.addEventListener("mousedown", (e) => e.preventDefault());
        b.addEventListener("click", (e) => {
          e.stopPropagation();
          fn();
        });
        tools.appendChild(b);
      };
      mkTool("split", "切", "從游標處斷句（Enter）", doSplit);
      mkTool("merge", "併", "併入前一句（句首 Backspace）", doMerge, i === 0);
      mkTool(
        "card",
        "卡",
        "做成標題卡（沿用這句的文字與時間）",
        () => addCardFromSub(i),
        false,
        "is-card",
      );
      mkTool(
        "delete",
        "刪",
        "刪掉這句字幕（⌘⌫）",
        doDelete,
        false,
        "is-danger",
      );
      row.appendChild(tools);

      list.appendChild(row);
    });
    linesRebuilding = false;
    const count = $("vt-line-count");
    if (count) count.textContent = `${state.subs.length} 句`;
    syncLineCardMarks();
    syncPlayingLine(true);
  }

  // 已升格的句子在右欄標出來 —— 它的字幕不會再燒一次，
  // 使用者要能一眼看出「這句現在是靠標題卡在顯示」。
  function syncLineCardMarks() {
    const list = $("vt-line-list");
    if (!list) return;
    list.querySelectorAll(".vt-line").forEach((row) => {
      const sub = state.subs[Number(row.dataset.i)];
      if (!sub) return;
      const has = !!cardFromSub(sub);
      row.classList.toggle("is-carded", has);
      const btn = row.querySelector(".vt-line-tool.is-card");
      if (btn) {
        btn.classList.toggle("is-on", has);
        btn.title = has
          ? "已做成標題卡（這句字幕不再重複顯示）"
          : "做成標題卡（沿用這句的文字與時間）";
      }
    });
  }

  // 播放頭落在哪一句 → 右欄那列亮起來並自動捲進視野。
  // 少了這個，播放時眼睛得在時間軸和清單之間自己對，改字前要先找半天。
  let lastPlayingLine = -1;
  function syncPlayingLine(force) {
    const list = $("vt-line-list");
    if (!list) return;
    const v = $("vt-video");
    const t = (v && v.currentTime) || 0;
    let idx = -1;
    for (let i = 0; i < state.subs.length; i++) {
      const x = state.subs[i];
      if (t >= x.start && t < x.end) {
        idx = i;
        break;
      }
    }
    if (!force && idx === lastPlayingLine) return;
    lastPlayingLine = idx;
    list.querySelectorAll(".vt-line").forEach((row) => {
      row.classList.toggle("is-playing", Number(row.dataset.i) === idx);
    });
    // 正在改字時不要把清單捲走（游標會離開視野）
    if (idx >= 0 && state.editingSub == null) {
      const row = list.querySelector(`.vt-line[data-i="${idx}"]`);
      if (row) row.scrollIntoView({ block: "nearest" });
    }
  }

  // ── 字幕樣式：即時預覽 ──────────────────────────────────────────
  // 換算（ASS → CSS）全部在 timeline-core.js 的「字幕樣式」段，podcast 模式吃同一份；
  // 本檔只負責把算出來的 CSS 指派到 DOM 上。
  function positionPreview(wrap, alignment, marginPx) {
    Object.assign(wrap.style, subtitlePositionCss(alignment, marginPx));
  }

  // 把面板控制項的顯示值同步成 state（供程式化改值後刷新 UI）
  function syncStyleControls() {
    const st = state.style;
    $("st-font").value = st.font_name;
    $("st-size").value = st.font_size;
    $("st-size-val").textContent = st.font_size;
    $("st-primary").value = st.primary_colour_hex;
    $("st-primary-val").textContent = hexToAss(st.primary_colour_hex);
    $("st-outline-col").value = st.outline_colour_hex;
    $("st-outline-col-val").textContent = hexToAss(st.outline_colour_hex);
    $("st-outline").value = st.outline;
    $("st-outline-val").textContent = st.outline;
    $("st-shadow").value = st.shadow;
    $("st-shadow-val").textContent = st.shadow;
    $("st-marginv").value = st.margin_v;
    $("st-marginv-val").textContent = st.margin_v;
    segSet("st-bold", st.bold);
    segSet("st-border", st.border_style);
    segSet("st-align", st.alignment);
  }

  function segSet(id, v) {
    document
      .querySelectorAll(`#${id} button`)
      .forEach((b) =>
        b.classList.toggle("is-on", String(b.dataset.v) === String(v)),
      );
  }

  // 播放頭當下所在的句；落在兩句之間的空隙就回 null（那時本來就沒人在說話）
  function subAt(t) {
    return state.subs.find((s) => t >= s.start && t < s.end) || null;
  }

  // 畫面上那行預覽字幕該顯示什麼。空字串＝這一刻不該有字。
  function currentSubText() {
    const v = $("vt-video");
    const t = (v && v.currentTime) || 0;
    const s = subAt(t);
    if (s) {
      // 已升格成標題卡的句子不再燒第二次：畫面上那張卡就是這句話，
      // 兩份一起顯示只會疊成兩行一模一樣的字。判準與輸出的
      // coveredByCard 是同一個函式，預覽才等於成品。
      if (subCoveredByCard(s)) return "";
      return s.text;
    }
    // 空隙照實留白。這裡原本會塞第一句當示範，結果播放中每過一個空隙就
    // 閃一次不相干的字（24 秒的示範片有 10 個空隙＝閃 10 次，最短只停 0.3 秒）。
    // 預覽的規矩是「畫面永遠等於真實輸出」，沒人說話就不該有字。
    // 只有「暫停下來調樣式」時才給一句示範文字，否則看不到樣式套起來長怎樣。
    const adv = $("vt-adv");
    const tuning = v && v.paused && adv && adv.open;
    if (!tuning) return "";
    return state.subs.length ? state.subs[0].text : "字幕樣式即時預覽";
  }

  // 展開／收合樣式面板會改變「該不該顯示示範句」，當下就要反映
  (function bindAdvToggle() {
    const adv = $("vt-adv");
    if (adv) adv.addEventListener("toggle", () => updateSubPreview());
  })();

  function updateSubPreview() {
    const el = $("vt-sub-preview-text");
    if (!el) return;
    const txt = currentSubText();
    if (el.textContent !== txt) el.textContent = txt; // 避免每幀 churn
    // 沒字就整塊收掉：不透明底色塊樣式下，空字串會留一塊沒有字的底色
    const wrap = $("vt-sub-preview");
    if (wrap && wrap.hidden !== !txt) wrap.hidden = !txt;
  }

  // 依 state.style 即時套用到影片上的預覽字幕（任何控制項一改就呼叫）
  function applyStyle() {
    syncStyleControls();
    const st = state.style;
    const v = $("vt-video");
    const wrap = $("vt-sub-preview");
    const el = $("vt-sub-preview-text");
    if (!wrap || !el) return;
    // ASS 字級是相對 1080p；預覽依實際影片高度等比縮放
    const vh = v && v.clientHeight ? v.clientHeight : 360;
    const scale = vh / 1080;
    Object.assign(el.style, buildSubtitleCss(st, scale));
    positionPreview(wrap, st.alignment, st.margin_v * scale);
    updateSubPreview();
  }

  function bindStylePanel() {
    $("st-font").addEventListener("change", (e) => {
      state.style.font_name = e.target.value;
      applyStyle();
    });
    $("st-size").addEventListener("input", (e) => {
      state.style.font_size = +e.target.value;
      applyStyle();
    });
    $("st-primary").addEventListener("input", (e) => {
      state.style.primary_colour_hex = e.target.value;
      applyStyle();
    });
    $("st-outline-col").addEventListener("input", (e) => {
      state.style.outline_colour_hex = e.target.value;
      applyStyle();
    });
    $("st-outline").addEventListener("input", (e) => {
      state.style.outline = +e.target.value;
      applyStyle();
    });
    $("st-shadow").addEventListener("input", (e) => {
      state.style.shadow = +e.target.value;
      applyStyle();
    });
    $("st-marginv").addEventListener("input", (e) => {
      state.style.margin_v = +e.target.value;
      applyStyle();
    });
    bindSeg("st-bold", (val) => {
      state.style.bold = +val;
      applyStyle();
    });
    bindSeg("st-border", (val) => {
      state.style.border_style = +val;
      applyStyle();
    });
    bindSeg("st-align", (val) => {
      state.style.alignment = +val;
      applyStyle();
    });
  }

  function bindSeg(id, cb) {
    document.querySelectorAll(`#${id} button`).forEach((b) => {
      b.addEventListener("click", () => cb(b.dataset.v));
    });
  }

  // ── 播放頭同步（T1：core 抽到 timeline-core.js，與 podcast 模式共用）───
  function updatePlayhead() {
    const v = $("vt-video");
    const ph = $("vt-playhead");
    if (!v || !ph || !(state.duration > 0)) return;
    const grip = $("vt-playhead-grip");
    const scroll = $("vt-tl-scroll");
    const tl = $("vt-timeline");
    // 影片模式沒有窗口化時間軸，t0 恆為 0（整片）
    positionPlayhead({
      t: v.currentTime,
      t0: 0,
      total: state.duration,
      elPh: ph,
      elGrip: grip,
      elScroll: scroll,
      elTl: tl,
    });
    // 只標了單邊時，範圍的另一端就是播放頭 → 播放頭動就得重畫
    if ((state.mark.in == null) !== (state.mark.out == null)) renderSelection();
  }

  function updateTimeReadout() {
    const v = $("vt-video");
    $("vt-time").textContent = `${fmt(v.currentTime)} / ${fmt(state.duration)}`;
  }

  // 播放時跳過剪除段：游標落進某剪除段 → 跳到該段結尾，體現「剪後效果」
  function applySkipPreview() {
    if (!state.skipPreview) return;
    const v = $("vt-video");
    // 暫停微調時不彈開：使用者常要跳進剪除段確認「這段到底剪掉了什麼」，
    // 被自動彈走會像點了沒反應。播放時照舊跳過，剪後效果不受影響。
    if (v.paused) return;
    const t = v.currentTime;
    for (const [s, e] of state.cuts) {
      // 留 1e-3 邊界避免浮點抖動；跳到片尾就直接暫停
      if (t >= s - 1e-3 && t < e - 1e-3) {
        if (e >= state.duration - 1e-3) {
          v.pause();
          v.currentTime = state.duration;
        } else {
          v.currentTime = e;
        }
        return;
      }
    }
  }

  function seekTo(t) {
    const v = $("vt-video");
    if (!v) return;
    v.currentTime = clamp(t, 0, state.duration);
    syncAfterSeek();
  }
  function syncAfterSeek() {
    updatePlayhead();
    updateTimeReadout();
    updateSubPreview();
    syncPlayingLine(false);
    updateCardLayer(false);
  }

  let rafId = null;
  function frame() {
    const v = $("vt-video");
    applySkipPreview();
    updatePlayhead();
    updateTimeReadout();
    updateSubPreview(); // 播放中預覽字幕文字跟著目前字幕句走
    syncPlayingLine(false); // 右欄清單跟著亮，掃視時找得到「現在唸到哪」
    updateCardLayer(false); // 標題卡疊層跟著播放頭切換
    if (!v.paused && !v.ended) rafId = requestAnimationFrame(frame);
    else rafId = null;
  }
  function startLoop() {
    if (rafId == null) rafId = requestAnimationFrame(frame);
  }

  // ── 縮放（T1：core 抽到 timeline-core.js，與 podcast 模式共用）───────
  const ZOOM_MIN = 1,
    ZOOM_MAX = 60,
    ZOOM_STEP = 1.6;
  function applyZoomWidth() {
    // 縮放套在三軌共用容器上；timeline / 字幕軌 / 剪後軌各 width:100% 自動跟著同寬
    $("vt-tracks").style.width = `${state.tlZoom * 100}%`;
    $("vt-zoom-out").disabled = state.tlZoom <= ZOOM_MIN + 1e-6;
    $("vt-zoom-in").disabled = state.tlZoom >= ZOOM_MAX - 1e-6;
  }
  function setZoom(z, anchorClientX = null) {
    const scroll = $("vt-tl-scroll");
    const tl = $("vt-timeline");
    const applied = applyTimelineZoom({
      z,
      currentZoom: state.tlZoom,
      zoomMin: ZOOM_MIN,
      zoomMax: ZOOM_MAX,
      scroll,
      tl,
      anchorClientX,
      setZoomValue: (v) => {
        state.tlZoom = v;
      },
      applyWidth: applyZoomWidth,
    });
    if (applied == null) return;
    drawWaveform(); // 寬度變了 → 波形重畫一次
    render();
  }

  // ── 拖曳框選 → 標記剪除 ─────────────────────────────────────────
  function timeFromEvent(e) {
    const tl = $("vt-timeline");
    const rect = tl.getBoundingClientRect();
    return clamp(
      ((e.clientX - rect.left) / rect.width) * state.duration,
      0,
      state.duration,
    );
  }

  // ── 播放頭拖曳刮動（drag-to-scrub）───────────────────────────────
  // 把手（#vt-playhead-grip）是 #vt-timeline 的子節點，換算時間直接沿用
  // timeFromEvent（已經用 #vt-timeline 的 getBoundingClientRect 換算，
  // 縮放/捲動後 rect 會反映實際版面，不用另外處理 zoom）。
  // pointerdown 在把手上會 stopPropagation，不冒泡到 onTimelinePointerDown，
  // 所以不會誤觸框選剪段；框選剪段本身完全沒被改動。
  function bindPlayheadScrub() {
    const grip = $("vt-playhead-grip");
    if (!grip) return;
    grip.addEventListener("pointerdown", (e) => {
      if (e.button != null && e.button !== 0) return;
      // 沒有影片或長度未知（duration=0）→ 靜默不做事，不拋例外
      if (!(state.duration > 0)) return;
      e.preventDefault();
      e.stopPropagation();
      grip.classList.add("is-scrubbing");
      // 某些環境（自動化、觸控筆切換）setPointerCapture 會丟例外，
      // 抓不到就算了，監聽掛在 window 上，拖出把手外一樣收得到。
      try {
        grip.setPointerCapture(e.pointerId);
      } catch (_) {}
      seekTo(timeFromEvent(e)); // 按下當下就先跳一次，不用等第一個 move
      const move = (ev) => seekTo(timeFromEvent(ev));
      const up = () => {
        grip.classList.remove("is-scrubbing");
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }

  let dragStartX = 0;
  function onTimelinePointerDown(e) {
    // 點在剪除段上 → 交給該段自己的 click 處理（取消），不啟動選區
    if (e.target.closest(".vt-cut")) return;
    // 點在播放頭刮動把手上 → 交給 bindPlayheadScrub 處理（它會 stopPropagation，
    // 正常不會冒泡到這裡；多一層防呆避免日後改動順序時誤觸框選）
    if (e.target.closest(".vt-playhead-grip")) return;
    if (e.button != null && e.button !== 0) return;
    e.preventDefault();
    dragStartX = e.clientX;
    const t = timeFromEvent(e);
    state.selecting = { a: t, b: t };
    renderSelection();
    window.addEventListener("pointermove", onTimelinePointerMove);
    window.addEventListener("pointerup", onTimelinePointerUp);
  }
  function onTimelinePointerMove(e) {
    if (!state.selecting) return;
    state.selecting.b = timeFromEvent(e);
    renderSelection();
  }
  // 一句話的短提示。用在「操作合法但沒生效」的地方 ——
  // 靜默失敗會被讀成「這個功能壞了」。
  let toastTimer = null;
  // ── 匯出／匯入剪輯指令 ──────────────────────────────────────────
  // 原型的終點：把時間軸上的操作變成一份結構化指令，後端照著接片段、
  // 燒字幕、疊標題卡。沒有這一步，這個 UI 只是一台好看的播放器。
  const PLAN_VERSION = 1;

  const r3 = (n) => Math.round(n * 1000) / 1000;
  const r4 = (n) => Math.round(n * 10000) / 10000;

  // ── 對齊位移數學（與 app.js / api.js 對稱）───────────────────────────
  // 顯示軸位移 totalShift：把磁碟 _v2.srt（外接音檔軸）的字幕時間搬到 cam A 軸，
  // 讓播放預覽的字幕 highlight 對得上 video.currentTime。
  //   totalShift = (有外接音檔 ? -audioSyncOffset : 0) + subtitleOffsetSec
  //   載入：display = disk + totalShift（見 applyLoadShiftToSubs）
  //   存檔：disk = display − totalShift（見 toDiskTime）
  // 兩向保證對稱（存→重載不會越存越早）；demo 或零偏移時 = 0，行為不變。
  function alignShift() {
    const audioShift =
      state.audioPath && state.audioSyncOffset ? -state.audioSyncOffset : 0;
    return audioShift + (state.subtitleOffsetSec || 0);
  }
  // 時間軸還原：cam A 軸顯示時間 → 磁碟 _v2.srt 時間。必與 alignShift 對稱。
  function toDiskTime(t) {
    const off = alignShift();
    return { start: r3(t.start - off), end: r3(t.end - off) };
  }
  // 把「剛從 /api/subtitles 載入的原始（磁碟軸）字幕」整批 +totalShift 移到 cam A 軸。
  // 只在剛載入原始字幕後呼叫一次，避免重複位移。
  function applyLoadShiftToSubs() {
    const sh = alignShift();
    if (!sh) return;
    state.subs = state.subs.map((s) => ({
      ...s,
      start: r3(s.start + sh),
      end: r3(s.end + sh),
    }));
  }

  // cuts 的補集＝真正要保留、按順序接起來的片段
  function keepSegments() {
    const segs = [];
    let cur = 0;
    state.cuts.forEach(([s, e]) => {
      if (s > cur) segs.push([cur, s]);
      cur = Math.max(cur, e);
    });
    if (cur < state.duration) segs.push([cur, state.duration]);
    return segs;
  }

  // 原始時間 → 剪後時間軸上的時間。落在剪除段裡的時間點一律歸到接縫
  // （＝該段被接起來之後的位置），這樣被部分剪掉的字幕不會報出一個
  // 剪後根本不存在的時間。
  function toOutTime(t, keep) {
    let out = 0;
    for (const [s, e] of keep) {
      if (t <= s) return out;
      if (t < e) return out + (t - s);
      out += e - s;
    }
    return out;
  }

  function buildPlan() {
    const keep = keepSegments();
    // 與保留片段沒有交集 = 這句／這張卡整段被剪掉了，後端不用處理
    const hit = (a, b) =>
      keep.some(([s, e]) => Math.min(b, e) - Math.max(a, s) > 1e-6);
    const stamp = (o) =>
      Object.assign({}, o, {
        outStart: r3(toOutTime(o.start, keep)),
        outEnd: r3(toOutTime(o.end, keep)),
        dropped: !hit(o.start, o.end),
      });
    return {
      version: PLAN_VERSION,
      source: DEMO ? "demo" : "real",
      duration: r3(state.duration),
      finalDuration: r3(keep.reduce((a, [s, e]) => a + (e - s), 0)),
      cuts: state.cuts.map(([s, e]) => [r3(s), r3(e)]),
      keep: keep.map(([s, e]) => [r3(s), r3(e)]),
      // coveredByCard：這句已經有標題卡在演，後端不要再燒一次。
      // 沒有這個欄位的話，後端只會看到「一句字幕」和「一張同文字的卡」，
      // 兩個都燒上去 —— 那是成品裡真的疊出兩行一樣的字，比預覽出錯嚴重。
      // start/end 寫「磁碟 _v2.srt（外接音檔軸）」＝載入位移的逆運算 toDiskTime，
      // 才能跟磁碟一致、避免每次「存→重載」都再被移一次 totalShift（症狀：字幕越存越早）。
      // out*/dropped 仍以 cam A 軸（顯示軸）算 —— 那是衍生值、後端會依 cuts 重算。
      subtitles: state.subs.map((x) => {
        const disk = toDiskTime(x);
        const s = stamp({
          start: r3(x.start),
          end: r3(x.end),
          text: x.text,
          coveredByCard: subCoveredByCard(x),
        });
        s.start = disk.start;
        s.end = disk.end;
        return s;
      }),
      // fromSubtitle：這張卡從哪一句升格來的。血緣本來只活在記憶體裡，
      // 匯出再匯入就靠時間硬湊 —— 卡一旦被拖過長度就湊不回去，字幕會重新
      // 冒出來。寫進指令才活得過一次 round-trip。
      cards: state.titleCards.map((c) => {
        const o = stamp({
          start: r3(c.start),
          end: r3(c.end),
          tpl: c.tpl,
          text: c.text,
          scale: c.scale,
          x: r4(c.x),
          y: r4(c.y),
          fromSubtitle: c.src
            ? { start: r3(c.src.start), end: r3(c.src.end) }
            : null,
        });
        // 卡片 drop 判準與後端 title_cards 對齊：起點落在被移除區間（含片頭/片尾裁切）
        // 即不出現在成品。stamp 的 !hit 是字幕用的「部分覆蓋」語意，對卡片會漏掉
        // 「起點在裁切區、卻延伸到保留區」的卡 —— 那正是片頭卡靜默丟棄的來源。
        o.dropped = cardDropped(c);
        return o;
      }),
      style: Object.assign({}, state.style),
    };
  }

  // 匯入：只吃 cuts / subtitles / cards / style 這四個真來源。
  // keep 與 out* 是算出來的衍生值，一律重算 —— 外面改了衍生值也不該
  // 影響結果，否則同一份指令會有兩個互相矛盾的真相。
  // 形狀不合就丟例外（呼叫端顯示錯誤），不做「盡量吃一點」的容錯：
  // 半套進來的剪輯指令比明講失敗更難發現。
  function applyPlan(obj) {
    if (!obj || typeof obj !== "object" || Array.isArray(obj))
      throw new Error("這不是一份剪輯指令（應為 JSON 物件）");
    if (obj.version !== PLAN_VERSION)
      throw new Error(`版本不合：需要 ${PLAN_VERSION}，收到 ${obj.version}`);
    ["cuts", "subtitles", "cards"].forEach((k) => {
      if (!Array.isArray(obj[k])) throw new Error(`缺少或格式錯誤：${k}`);
    });
    const num = (v, label) => {
      const n = +v;
      if (!Number.isFinite(n)) throw new Error(`${label} 不是數字：${v}`);
      return n;
    };
    const cuts = obj.cuts.map((c, i) => {
      if (!Array.isArray(c) || c.length !== 2)
        throw new Error(`cuts[${i}] 應為 [起, 迄]`);
      const a = num(c[0], `cuts[${i}][0]`);
      const b = num(c[1], `cuts[${i}][1]`);
      if (b <= a) throw new Error(`cuts[${i}] 迄點不大於起點`);
      return [a, b];
    });
    // 指令裡的字幕 start/end 是磁碟軸（buildPlan 用 toDiskTime 寫下）；讀回來 +totalShift
    // 移回 cam A 顯示軸，export→import 才是 identity，卡片 fromSubtitle 時間比對也才對得上。
    const sh = alignShift();
    const subs = obj.subtitles.map((x, i) => ({
      start: r3(num(x.start, `subtitles[${i}].start`) + sh),
      end: r3(num(x.end, `subtitles[${i}].end`) + sh),
      text: String(x.text == null ? "" : x.text),
    }));
    const cards = obj.cards.map((c, i) => {
      const tpl = CARD_TEMPLATES[c.tpl] ? c.tpl : "big";
      const d = CARD_TEMPLATES[tpl];
      const x = num(c.x, `cards[${i}].x`);
      const y = num(c.y, `cards[${i}].y`);
      return {
        id: ++cardSeq,
        start: num(c.start, `cards[${i}].start`),
        end: num(c.end, `cards[${i}].end`),
        tpl,
        text: String(c.text == null ? "" : c.text),
        scale: num(c.scale, `cards[${i}].scale`),
        x,
        y,
        // moved 不進指令（那是 UI 內部狀態）；用「有沒有離開版型預設位」
        // 反推，換版型時才不會把使用者擺過的位置洗掉。
        moved: Math.abs(x - d.x) > 1e-6 || Math.abs(y - d.y) > 1e-6,
        // 血緣：指令裡寫了 fromSubtitle 就照認（這是唯一可靠的來源，卡被
        // 拖過長度也不影響）。沒寫的是舊版指令或手寫的，退回時間對齊猜一次；
        // 猜不到就是沒有血緣，行為退回舊規則，不硬湊。
        src: (() => {
          const f = c.fromSubtitle;
          if (f && typeof f === "object") {
            return {
              start: num(f.start, `cards[${i}].fromSubtitle.start`),
              end: num(f.end, `cards[${i}].fromSubtitle.end`),
            };
          }
          if (f != null) throw new Error(`cards[${i}].fromSubtitle 應為物件`);
          const a = num(c.start, `cards[${i}].start`);
          const b = num(c.end, `cards[${i}].end`);
          const hit = subs.find(
            (x) => Math.abs(x.start - a) < 1e-6 && Math.abs(x.end - b) < 1e-6,
          );
          return hit ? { start: hit.start, end: hit.end } : null;
        })(),
      };
    });
    pushUndo("import");
    endUndoGroup();
    state.cuts = cuts;
    state.subs = subs;
    state.titleCards = cards;
    state.selectedCard = null;
    state.editingCard = null;
    state.editingSub = null;
    if (obj.style && typeof obj.style === "object")
      Object.assign(state.style, obj.style);
    applyStyle();
    render();
    renderLines();
    renderCardTrack();
    return { cuts: cuts.length, subs: subs.length, cards: cards.length };
  }

  // [起, 迄] 這種數字對被 JSON.stringify 攤成四行，十幾段剪除就要捲半天。
  // 摺回一行，人才讀得下去（格式仍是合法 JSON，貼回來照樣吃）。
  function prettyPlan(plan) {
    return JSON.stringify(plan, null, 2).replace(
      /\[\s+(-?[\d.]+),\s+(-?[\d.]+)\s+\]/g,
      "[$1, $2]",
    );
  }

  function openPlanDialog() {
    const plan = buildPlan();
    const dlg = $("vt-plan");
    $("vt-plan-json").value = prettyPlan(plan);
    const dropped =
      plan.subtitles.filter((x) => x.dropped).length +
      plan.cards.filter((x) => x.dropped).length;
    $("vt-plan-sum").textContent =
      `保留 ${plan.keep.length} 段 · 剪除 ${plan.cuts.length} 段 · ` +
      `${fmt(plan.duration)} → ${fmt(plan.finalDuration)} · ` +
      `字幕 ${plan.subtitles.length} 句 · 標題卡 ${plan.cards.length} 張` +
      (dropped ? ` · 其中 ${dropped} 項落在剪除段` : "");
    $("vt-plan-msg").textContent = "";
    dlg.hidden = false;
  }

  function bindPlanDialog() {
    $("vt-plan-open").addEventListener("click", openPlanDialog);
    $("vt-plan-close").addEventListener("click", () => {
      $("vt-plan").hidden = true;
    });
    $("vt-plan").addEventListener("click", (e) => {
      if (e.target.id === "vt-plan") $("vt-plan").hidden = true; // 點背景關閉
    });
    $("vt-plan-copy").addEventListener("click", async () => {
      const ta = $("vt-plan-json");
      ta.select();
      try {
        await navigator.clipboard.writeText(ta.value);
        toast("剪輯指令已複製");
      } catch (_) {
        // 沒有剪貼簿權限時已經幫他全選了，講清楚下一步而不是靜靜失敗
        toast("複製失敗，已幫你全選 —— 請按 ⌘C");
      }
    });
    $("vt-plan-apply").addEventListener("click", () => {
      const msg = $("vt-plan-msg");
      try {
        const r = applyPlan(JSON.parse($("vt-plan-json").value));
        msg.textContent = "";
        $("vt-plan").hidden = true;
        toast(
          `已套用：剪除 ${r.cuts} 段、字幕 ${r.subs} 句、標題卡 ${r.cards} 張`,
        );
      } catch (err) {
        msg.textContent = `套用失敗：${err.message}`;
      }
    });
    $("vt-plan-push").addEventListener("click", () => pushPlan(false));
    $("vt-plan-render").addEventListener("click", () => pushPlan("preview"));
    // 頂列主鈕：一鍵從當下時間軸出片，不用先開 JSON 面板
    $("vt-export").addEventListener("click", () => pushPlan(true, buildPlan()));
    $("vt-preview").addEventListener("click", () =>
      pushPlan("preview", buildPlan()),
    );
    $("vt-targets").addEventListener("change", () => {
      syncTargets();
      if (readTargets().length) planMsg("", "");
    });
    $("vt-render-cancel").addEventListener("click", cancelRender);
    $("vt-render-reveal").addEventListener("click", revealOutput);
    $("vt-render-close").addEventListener("click", hideRender);
  }

  // ── 送到後端 ───────────────────────────────────────────────
  // 這是原型唯一有副作用的動作：會覆寫這一集的 _v2.srt 與 episode.yaml。
  // 所以 (1) demo 資料要先問過，(2) 送出中一律鎖鈕，(3) 失敗訊息留在面板上
  // 不用 toast —— toast 2.2 秒就消失，出錯的人來不及讀。
  // 頂列的「輸出影片」與面板裡的按鈕共用同一條路，所以狀態要同時寫兩處
  // ——不然關掉面板的人就看不到自己按下去發生了什麼事。
  function planMsg(suffix, text) {
    for (const [id, base] of [
      ["vt-plan-msg", "vt-plan-msg"],
      ["vt-export-msg", "vt-export-msg"],
    ]) {
      const el = $(id);
      if (!el) continue;
      el.className = base + suffix;
      if (text !== undefined) el.textContent = text;
    }
  }

  // 送出與合成共用同一組鈕：送出中鎖、合成中也要續鎖
  //（後端在合成時收 apply-plan 會回 409，前端先擋住比讓人撞牆好）
  // 出片目標：yt=橫式 16:9、reels=直式 9:16、mp3=純音檔（後端 assemble_job 只認這三個）
  function readTargets() {
    return Array.from(
      document.querySelectorAll("#vt-targets input:checked"),
    ).map((el) => el.value);
  }

  // 合成中：這時候鈕本來就該鎖著，改勾目標不可以把它解開
  let planBusy = false;

  // 一顆目標都沒選就沒東西可出，鈕要擋住並講原因，不能讓人按下去才收到後端 400。
  // 勾回來也要能解鎖，否則使用者會卡死在自己按出來的禁用狀態。
  function syncTargets() {
    const none = readTargets().length === 0;
    const box = $("vt-targets");
    if (box) box.classList.toggle("is-empty", none);
    for (const id of ["vt-export", "vt-preview", "vt-plan-render"]) {
      const el = $(id);
      if (el) el.disabled = planBusy || none;
    }
    // 這行擠在頂列右側（max-width 34ch），寫長了會折成兩行把版面撐開
    if (none) planMsg("", "至少要選一個輸出目標");
  }

  function setPlanButtonsDisabled(disabled) {
    planBusy = disabled;
    for (const id of [
      "vt-plan-push",
      "vt-plan-render",
      "vt-plan-apply",
      "vt-export",
      "vt-preview",
    ]) {
      const el = $(id);
      if (el) el.disabled = disabled;
    }
    // 解鎖時仍要尊重「沒選目標」這個更前面的擋門
    if (!disabled) syncTargets();
  }

  function setPlanBusy(busy, text) {
    setPlanButtonsDisabled(busy);
    planMsg(busy ? " is-busy" : "", text);
  }

  // planOverride：頂列主鈕直接拿當下時間軸出片，不經過 JSON textarea
  //（面板沒開時 textarea 是舊的，靠它會送出過期的指令）
  // 出片前的合理性檢查：只擋「做出來一定是壞成品」的情況，其餘用 confirm 提醒。
  // 純套用（render 為 false）不檢查——那只是寫檔，沒有成品可壞。
  // finalDuration 缺欄位時不擋：手貼的舊指令沒有這個衍生值，不該被誤殺。
  function checkBeforeRender(plan) {
    if (typeof plan.finalDuration === "number" && plan.finalDuration <= 0) {
      planMsg("", "剪後長度是 0，沒有東西可以合成");
      return false;
    }
    // 卡被剪掉是意外（字幕被剪掉是理所當然，所以不提醒字幕）
    const lost = (plan.cards || []).filter((c) => c.dropped).length;
    if (lost) {
      return confirm(
        `有 ${lost} 張標題卡落在剪掉的段落裡，不會出現在成品中。仍要合成嗎？`,
      );
    }
    return true;
  }

  async function pushPlan(render, planOverride) {
    let plan = planOverride;
    if (!plan) {
      try {
        plan = JSON.parse($("vt-plan-json").value);
      } catch (err) {
        planMsg("", `JSON 壞掉了，沒有送出：${err.message}`);
        return null;
      }
    }
    // 送出一律覆蓋這一集的 _v2.srt，真集也一樣（舊版只在 demo 問，真集靜默覆蓋 ——
    // 但真集被蓋掉的是真字幕，代價更大）。demo 另外點名資料是假的。
    const warn =
      plan.source === "demo"
        ? "現在是示範資料，送出會用假字幕覆蓋這一集的 _v2.srt（舊檔會留 .bak）。確定要送？"
        : "送出會覆蓋這一集的 _v2.srt 與剪輯設定（舊檔會留 .bak）。確定要送？";
    if (!confirm(warn)) return null;
    if (render && !checkBeforeRender(plan)) return null;
    setPlanBusy(true, render ? "送出並合成中…" : "送出中…");
    try {
      const res = await fetch("/api/apply-plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          render
            ? {
                plan,
                targets: readTargets(),
                force: true,
                ...(render === "preview" ? { preview_sec: 30 } : {}),
              }
            : { plan },
        ),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      const a = body.applied || {};
      const parts = [
        `已寫入：剪除 ${a.cuts} 段、字幕 ${a.subtitles} 句`,
        a.style_keys && a.style_keys.length
          ? `樣式 ${a.style_keys.length} 項`
          : "",
        a.cards ? `標題卡 ${a.cards} 張` : "",
        // 空白卡燒出來只有底框，後端會略過——沒講的話使用者會以為有
        a.empty_cards_skipped ? `略過 ${a.empty_cards_skipped} 張空白卡` : "",
        body.assemble
          ? render === "preview"
            ? "已開始合成試看片"
            : "已開始合成影片"
          : "",
      ].filter(Boolean);
      setPlanBusy(false);
      planMsg(" is-ok", parts.join("｜"));
      toast(parts[0]);
      // 有開合成才盯進度；沒有的話（純套用）就到此為止
      if (body.assemble) {
        startRenderWatch(render === "preview" ? "試看片" : "影片");
      }
      return body;
    } catch (err) {
      setPlanBusy(false);
      // fetch 本身失敗（沒有後端／離線）跟後端回錯是兩件事，講清楚才知道要修哪邊
      planMsg(
        "",
        err instanceof TypeError
          ? "連不上後端 —— 這個原型要由 podcast 網頁伺服器提供才能送出"
          : `送出失敗：${err.message}`,
      );
      return null;
    }
  }

  // ── 合成進度 ─────────────────────────────────────────────────────
  // 按下「輸出影片」之後 ffmpeg 要跑幾分鐘。沒有這一段的話畫面只會停在
  // 「已開始合成影片」，人不知道好了沒、也沒得喊停。每秒 poll 後端的
  // /api/assemble/status，把 preparing / running / done / error / 取消
  // 五種結果都畫出來。
  let renderTimer = null;
  let renderFails = 0;
  let renderLabel = "影片";
  let renderOutputs = [];

  function fmtEta(sec) {
    if (sec === null || sec === undefined || !isFinite(sec)) return "";
    const s = Math.max(0, Math.round(sec));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  // kind: "" | "is-ok" | "is-error"；percent 為 null 代表進度未知
  function paintRender({
    kind = "",
    percent = null,
    text = "",
    live = false,
    title = "",
  }) {
    const box = $("vt-render");
    if (!box) return;
    box.hidden = false;
    box.className = "vt-render" + (kind ? " " + kind : "");
    const pct = percent === null ? 0 : Math.max(0, Math.min(100, percent));
    $("vt-render-fill").style.width = pct + "%";
    const bar = $("vt-render-bar");
    if (percent === null) bar.removeAttribute("aria-valuenow");
    else bar.setAttribute("aria-valuenow", String(Math.round(pct)));
    const label = $("vt-render-text");
    label.textContent = text;
    // 文字被 26ch 截斷時，滑鼠停留還看得到完整內容
    if (title) label.title = title;
    else label.removeAttribute("title");
    // 合成中只能取消；結束後換成「在 Finder 顯示」與收起
    $("vt-render-cancel").hidden = !live;
    $("vt-render-close").hidden = live;
    $("vt-render-reveal").hidden = live || !renderOutputs.length;
  }

  function stopRenderWatch() {
    clearInterval(renderTimer);
    renderTimer = null;
    setPlanButtonsDisabled(false);
  }

  function hideRender() {
    const box = $("vt-render");
    if (box) box.hidden = true;
  }

  async function pollRenderOnce() {
    let s;
    try {
      const res = await fetch("/api/assemble/status");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      s = await res.json();
      renderFails = 0;
    } catch (err) {
      // 網路瞬斷不該把還在跑的合成判死；連錯三次才放棄盯著
      renderFails += 1;
      if (renderFails >= 3) {
        stopRenderWatch();
        paintRender({
          kind: "is-error",
          text: "看不到進度了（連不上後端），合成可能仍在背景進行",
        });
      }
      return;
    }
    const state = s.state;
    if (state === "preparing") {
      paintRender({ text: `準備合成${renderLabel}…`, live: true });
      return;
    }
    if (state === "running") {
      const eta = fmtEta(s.eta_s);
      const step = s.total > 1 ? `（${(s.index || 0) + 1}/${s.total}）` : "";
      paintRender({
        percent: s.percent || 0,
        text: `合成${renderLabel}中 ${Math.round(s.percent || 0)}%${eta ? `・剩 ${eta}` : ""}${step}`,
        live: true,
      });
      return;
    }
    stopRenderWatch();
    if (state === "done") {
      renderOutputs = s.output_files || [];
      // 勾了 YT+Reels+MP3 就會有三個檔。26ch 放不下三個檔名，多檔就報數量，
      // 完整清單掛 title；只報最後一個的話使用者會以為只出了一支。
      const names = renderOutputs.map((p) => p.split("/").pop());
      paintRender({
        kind: "is-ok",
        percent: 100,
        text: !names.length
          ? "合成完成"
          : names.length === 1
            ? `合成完成・${names[0]}`
            : `合成完成・${names.length} 個檔案`,
        title: names.join("\n"),
      });
      toast("合成完成");
      return;
    }
    if (state === "error") {
      paintRender({
        kind: "is-error",
        text: `合成失敗：${s.error || "未知錯誤"}`,
      });
      return;
    }
    // idle：合成被取消（或後端重開）——不是成功，別畫成綠的
    paintRender({ text: "已取消合成" });
  }

  function startRenderWatch(label) {
    renderLabel = label || "影片";
    renderOutputs = [];
    renderFails = 0;
    setPlanButtonsDisabled(true);
    paintRender({ text: `準備合成${renderLabel}…`, live: true });
    clearInterval(renderTimer);
    renderTimer = setInterval(pollRenderOnce, 1000);
    pollRenderOnce();
  }

  async function cancelRender() {
    const btn = $("vt-render-cancel");
    if (btn) btn.disabled = true;
    paintRender({ text: "取消中…", live: true });
    try {
      await fetch("/api/assemble/cancel", { method: "POST" });
    } catch (err) {
      // 取消打不出去也要講，不然按了沒反應會以為壞掉
      paintRender({ kind: "is-error", text: `取消失敗：${err.message}` });
    }
    if (btn) btn.disabled = false;
    await pollRenderOnce();
  }

  async function revealOutput() {
    if (!renderOutputs.length) return;
    try {
      await fetch("/api/reveal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: renderOutputs[0] }),
      });
    } catch (err) {
      paintRender({ kind: "is-error", text: `打不開資料夾：${err.message}` });
    }
  }

  // 開頁時後端可能已經在合成（別的分頁按的、或重整過）——接回去繼續盯
  async function attachRenderWatch() {
    try {
      const res = await fetch("/api/assemble/status");
      if (!res.ok) return;
      const s = await res.json();
      if (s.state === "preparing" || s.state === "running")
        startRenderWatch("影片");
    } catch (err) {
      /* 沒有後端（純靜態開檔）就當沒這回事 */
    }
  }

  function toast(msg) {
    const el = $("vt-toast");
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.hidden = true;
    }, 2200);
  }

  function onTimelinePointerUp(e) {
    window.removeEventListener("pointermove", onTimelinePointerMove);
    window.removeEventListener("pointerup", onTimelinePointerUp);
    const sel = state.selecting;
    state.selecting = null;
    if (!sel) return;
    const moved = Math.abs(e.clientX - dragStartX);
    if (moved < 3) {
      // 幾乎沒拖動 → 當作點擊定位（seek）
      seekTo(timeFromEvent(e));
      render();
      return;
    }
    // 拖了但不到 MIN_CUT：addCut 會擋掉，這裡要講出來，
    // 否則畫面完全沒變，看起來像剪除功能失靈。
    if (!addCut(sel.a, sel.b)) {
      toast(`拖太短了，不到 ${MIN_CUT} 秒的範圍不會剪除`);
    }
    render();
  }

  // ── 事件綁定 ────────────────────────────────────────────────────
  function bindEvents() {
    const v = $("vt-video");
    $("vt-timeline").addEventListener("pointerdown", onTimelinePointerDown);
    bindPlayheadScrub();

    $("vt-play").addEventListener("click", () => {
      if (v.paused) v.play();
      else v.pause();
    });
    v.addEventListener("play", () => {
      $("vt-play").textContent = "⏸ 暫停";
      startLoop();
    });
    v.addEventListener("pause", () => {
      $("vt-play").textContent = "▶ 播放";
    });
    v.addEventListener("seeked", () => {
      updatePlayhead();
      updateTimeReadout();
      syncPlayingLine(false);
    });
    v.addEventListener("timeupdate", () => {
      // rAF 沒在跑時（暫停微調）也要更新一次
      if (rafId == null) {
        applySkipPreview();
        updatePlayhead();
        updateTimeReadout();
        updateSubPreview();
        syncPlayingLine(false);
        updateCardLayer(false); // 暫停微調播放頭時，標題卡疊層也要跟著切
      }
    });

    // 右欄每個區塊的標頭都是收合鈕：改字幕時把標題卡收掉，清單就長回來
    document.querySelectorAll(".vt-side-sec > .vt-side-head").forEach((h) => {
      h.addEventListener("click", () => {
        const sec = h.parentElement;
        const open = sec.classList.toggle("is-collapsed") === false;
        h.setAttribute("aria-expanded", open ? "true" : "false");
        h.querySelector(".vt-side-caret").textContent = open ? "▾" : "▸";
      });
    });

    $("vt-skip").addEventListener("change", (e) => {
      state.skipPreview = e.target.checked;
    });
    $("vt-zoom-in").addEventListener("click", () =>
      setZoom(state.tlZoom * ZOOM_STEP),
    );
    $("vt-zoom-out").addEventListener("click", () =>
      setZoom(state.tlZoom / ZOOM_STEP),
    );

    window.addEventListener("keydown", (e) => {
      // ⌘Z 到哪都要能救（包含正在改字的時候），所以放在守門之前
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        undo();
        return;
      }
      // 焦點在輸入框時，單鍵一律讓給打字 —— 空白、方向鍵、Backspace 在
      // 那裡是字不是快捷鍵（原本空白鍵沒守門，在右欄改字會被吃掉）
      if (isTypingTarget(e.target)) return;
      // 帶 ⌘/Ctrl/Alt 的組合鍵留給系統與瀏覽器，不要搶
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      const key = e.key;
      if (key === " ") {
        e.preventDefault();
        if (v.paused) v.play();
        else v.pause();
      } else if (key === "ArrowLeft" || key === "ArrowRight") {
        // 微調用小步；按住 ⇧ 跨大步，找剪點時不用連按幾十下
        e.preventDefault();
        const step =
          (e.shiftKey ? NUDGE_BIG : NUDGE_SMALL) *
          (key === "ArrowLeft" ? -1 : 1);
        seekTo(v.currentTime + step);
      } else if (key === "i" || key === "I") {
        e.preventDefault();
        setMark("in");
      } else if (key === "o" || key === "O") {
        e.preventDefault();
        setMark("out");
      } else if (key === "Backspace" || key === "Delete") {
        e.preventDefault();
        // 選取了標題卡就刪這張卡（取代原本卡右上角的 × 鈕）；
        // 沒選卡才退回「剪除進出點圈起的範圍」的原意
        if (state.selectedCard != null) {
          removeCard(state.selectedCard);
        } else {
          cutMarked();
        }
      } else if (key === "Escape") {
        if (state.mark.in == null && state.mark.out == null) return;
        e.preventDefault();
        clearMark();
      }
    });

    window.addEventListener("resize", () => {
      drawWaveform();
      render();
      applyStyle(); // 影片尺寸變 → 預覽字級等比重算
    });
  }

  // ── 載入（雙模式）──────────────────────────────────────────────
  async function loadWaveformData() {
    const url = DEMO ? "sample-waveform.json" : "/api/waveform";
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) return null;
      const data = await r.json();
      if (!data || !Array.isArray(data.peaks) || !data.peaks.length)
        return null;
      return data;
    } catch (_) {
      return null;
    }
  }

  // 字幕：demo 讀 static 假資料，真模式讀後端正典字幕（_final_v2.srt）。
  // 失敗回 null 而不是空陣列 —— 空陣列是「這集真的一句字幕都沒有」的合法狀態，
  // 兩者混在一起，載入失敗就會靜默偽裝成「沒字幕」。
  async function loadSubs() {
    const url = DEMO ? "sample-subtitles.json" : "/api/subtitles";
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) return null;
      const data = await r.json();
      return Array.isArray(data.subs) ? data.subs : null;
    } catch (_) {
      return null;
    }
  }

  // 載入失敗提示：多個資源同時失敗時累加，不要後者蓋掉前者（蓋掉會漏診斷線索）
  function showEmptyNote(msg) {
    const note = $("vt-empty-note");
    note.textContent = note.hidden ? msg : `${note.textContent} ${msg}`;
    note.hidden = false;
  }

  function setDuration(d) {
    if (d > 0 && Math.abs(d - state.duration) > 0.01) {
      state.duration = d;
      // 播放列的「/ 總長」原本要等第一次 timeupdate 才寫進去 ——
      // 也就是使用者一載入頁面看到的是 00:00.0，播了才變對。初始態就補上。
      updateTimeReadout();
    }
  }

  // ── 對齊面板（與這集 episode.yaml 共用的五個欄位）─────────────────────
  // 對齊載入：真模式向 /api/episode 取這集對齊欄位（與 app.js loadEpisodeState 同源，
  // 但只取五個對齊純量 + audio.path + episode_dir）。四態：loading→success/error；demo 跳過。
  async function loadEpisodeAlignment() {
    if (DEMO) {
      state.align = { state: "demo", err: "" };
      return;
    }
    state.align = { state: "loading", err: "" };
    try {
      const r = await fetch("/api/episode", { cache: "no-store" });
      if (!r.ok) throw new Error(`/api/episode HTTP ${r.status}`);
      const d = await r.json();
      const numOr = (v, dflt) => {
        const n = +v;
        return Number.isFinite(n) ? n : dflt;
      };
      const audio = d.audio && typeof d.audio === "object" ? d.audio : null;
      state.audioPath = audio && audio.path ? String(audio.path) : "";
      state.audioSyncOffset = audio ? numOr(audio.sync_offset, 0) : 0;
      const cam =
        d.camera_sync_offset && typeof d.camera_sync_offset === "object"
          ? d.camera_sync_offset
          : null;
      state.camSyncOffsetB = cam ? numOr(cam.b, 0) : 0;
      state.headTrimSec = numOr(d.head_trim_sec, 0);
      state.tailTrimSec = numOr(d.tail_trim_sec, 0);
      state.subtitleOffsetSec = numOr(d.subtitle_offset_sec, 0);
      state.episodeDir = d.episode_dir ? String(d.episode_dir) : "";
      state.align = { state: "success", err: "" };
    } catch (err) {
      state.align = { state: "error", err: err.message || String(err) };
      showEmptyNote(`對齊設定載入失敗：${state.align.err}`);
    }
  }

  // 局部存檔 payload：只帶五個對齊鍵（+ episode_dir 防呆）。後端 save_state 用 key-presence
  // 語意：沒帶的鍵一律不動，所以 cards/cuts/splits 等其餘設定原封不動（比照 podcast 編輯器）。
  function buildAlignPayload() {
    const payload = {
      head_trim_sec: state.headTrimSec, // RAW，cam A 軸
      tail_trim_sec: state.tailTrimSec, // RAW
      camera_sync_offset_b: state.camSyncOffsetB, // 扁平鍵（後端存成 {b: val}）
      subtitle_offset_sec: state.subtitleOffsetSec,
      episode_dir: state.episodeDir, // 多分頁防呆：後端確認目標集
    };
    // 有外接音檔才送 audio；沒有就完全不帶這個鍵（避免把不存在的 audio pop 掉，
    // 也避免對「無音檔集」送 path:"" 造成語意混淆）。
    if (state.audioPath) {
      payload.audio = {
        path: state.audioPath,
        sync_offset: state.audioSyncOffset,
      };
    }
    return payload;
  }

  // 存對齊：POST /api/save 局部存檔 → 重新載入 /api/episode + /api/subtitles 並重套 totalShift，
  // 用「讀得回」證明寫入生效（round-trip）。async 期間鎖鈕；失敗顯示可見錯誤，不靜默。
  async function saveAlignment() {
    if (DEMO) return;
    const btn = $("vt-al-save");
    const status = $("vt-al-status");
    if (btn) btn.disabled = true;
    if (status) {
      status.className = "vt-align-status is-busy";
      status.textContent = "儲存中…";
    }
    try {
      const r = await fetch("/api/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildAlignPayload()),
      });
      if (!r.ok) throw new Error(`/api/save HTTP ${r.status}`);
      // 重載對齊 + 字幕，重套 totalShift → 畫面反映實際寫入磁碟的值
      await loadEpisodeAlignment();
      const subs = await loadSubs();
      state.subs = Array.isArray(subs) ? subs : [];
      applyLoadShiftToSubs();
      syncAlignInputs();
      renderLines();
      render();
      renderCardTrack();
      if (status) {
        status.className = "vt-align-status is-ok";
        status.textContent = "已儲存";
      }
    } catch (err) {
      if (status) {
        status.className = "vt-align-status is-err";
        status.textContent = `儲存失敗：${err.message || err}`;
      }
    } finally {
      if (btn) btn.disabled = DEMO;
    }
  }

  // 把 state 的五個對齊欄位灌回輸入框，並依四態設定停用狀態 / 提示文字。
  function syncAlignInputs() {
    const set = (id, v) => {
      const el = $(id);
      if (el) el.value = v;
    };
    set("vt-al-audio", state.audioSyncOffset);
    set("vt-al-camb", state.camSyncOffsetB);
    set("vt-al-head", state.headTrimSec);
    set("vt-al-tail", state.tailTrimSec);
    set("vt-al-sub", state.subtitleOffsetSec);
    const hasAudio = !!state.audioPath;
    // 沒外接音檔 → 聲音偏移無處可存，停用該欄（避免存了卻靜默無效）；demo 全欄停用
    const audioInput = $("vt-al-audio");
    if (audioInput) audioInput.disabled = DEMO || !hasAudio;
    ["vt-al-camb", "vt-al-head", "vt-al-tail", "vt-al-sub"].forEach((id) => {
      const el = $(id);
      if (el) el.disabled = DEMO;
    });
    const saveBtn = $("vt-al-save");
    if (saveBtn) saveBtn.disabled = DEMO;
    const note = $("vt-align-note");
    if (note) {
      const map = {
        loading: "載入中…",
        error: "載入失敗",
        success: hasAudio ? "有外接音檔" : "無外接音檔",
        demo: "demo（唯讀）",
        idle: "",
      };
      note.textContent = map[state.align.state] || "";
    }
    const hint = $("vt-align-hint");
    if (hint) {
      if (DEMO)
        hint.textContent = "demo 模式沒有真集：對齊欄位唯讀，不會寫任何檔。";
      else if (state.align.state === "error")
        hint.textContent = `對齊設定載入失敗：${state.align.err}`;
      else if (!hasAudio)
        hint.textContent =
          "影片／字幕時間對齊，與這集 episode.yaml 共用。此集無外接音檔，聲音偏移停用。";
      else
        hint.textContent =
          "影片／聲音／字幕時間對齊，與這集 episode.yaml 共用。";
    }
  }

  // 綁定五個數字欄位（change 寫回 state）與儲存鈕。這五個純量不套任何位移；
  // 位移只在載入 / 存檔後重載時套到字幕顯示時間。
  function bindAlignPanel() {
    const numOf = (el) => {
      const n = +el.value;
      return Number.isFinite(n) ? n : 0;
    };
    const bind = (id, apply) => {
      const el = $(id);
      if (el) el.addEventListener("change", () => apply(numOf(el)));
    };
    bind("vt-al-audio", (v) => (state.audioSyncOffset = v));
    bind("vt-al-camb", (v) => (state.camSyncOffsetB = v));
    bind("vt-al-head", (v) => (state.headTrimSec = Math.max(0, v)));
    bind("vt-al-tail", (v) => (state.tailTrimSec = Math.max(0, v)));
    bind("vt-al-sub", (v) => (state.subtitleOffsetSec = v));
    const btn = $("vt-al-save");
    if (btn) btn.addEventListener("click", saveAlignment);
  }

  async function init() {
    const badge = $("vt-mode-badge");
    badge.textContent = DEMO ? "原型 · demo" : "原型 · 真集";
    if (!DEMO) badge.classList.add("mode-real");

    const v = $("vt-video");
    v.src = DEMO ? "sample-video.mp4" : "/api/video";

    // 影片 metadata 提供權威時長；波形也有 duration，取兩者最大避免任一缺失
    v.addEventListener("loadedmetadata", () => {
      setDuration(v.duration);
      drawWaveform();
      render();
      applyStyle(); // 有了影片實際高度 → 預覽字級等比重算
    });
    v.addEventListener("error", () => {
      // 影片載入失敗（真模式沒開集/沒影片）→ 用波形時長撐住時間軸，不靜默假裝成功
      showEmptyNote(
        DEMO
          ? "找不到 sample-video.mp4（請從 static 目錄提供）。"
          : "影片載入失敗：真模式需要 app server 已開啟一集且該集有主影片（/api/video 回 200）。",
      );
    });

    const wf = await loadWaveformData();
    if (wf) {
      state.waveform = wf;
      setDuration(wf.duration || 0);
    } else {
      showEmptyNote(
        DEMO
          ? "找不到 sample-waveform.json。"
          : "波形載入失敗：真模式需要 app server 已開啟一集（/api/waveform 回 200）。",
      );
    }

    // 對齊欄位先載入：alignShift() 依賴這些值，字幕的顯示位移要用到（demo → 全 0、no-op）
    await loadEpisodeAlignment();

    const subs = await loadSubs();
    if (subs) {
      state.subs = subs;
    } else {
      state.subs = [];
      showEmptyNote(
        DEMO
          ? "找不到 sample-subtitles.json。"
          : "字幕載入失敗：真模式需要該集已轉好字幕（/api/subtitles 回 200，來源是 _final_v2.srt）。",
      );
    }
    // 原始（磁碟／外接音檔軸）字幕 +totalShift → cam A 顯示軸，highlight 才對得上影片
    applyLoadShiftToSubs();
    bindAlignPanel(); // 綁定對齊面板五欄 + 儲存鈕
    syncAlignInputs(); // 把載入到的對齊值灌進輸入框、依四態設定停用/提示
    bindStylePanel(); // 綁定字幕樣式面板（收在進階摺疊區）
    bindPlanDialog(); // 綁定「輸出剪輯指令」面板
    if (!DEMO) attachRenderWatch(); // 重整前若已在合成，接回去繼續顯示進度
    bindCardPanel(); // 綁定標題卡：版型縮圖／大小／回預設位／拖放
    applyStyle(); // 初始樣式套上預覽（先用 fallback 高度，metadata 到齊會再算）
    renderLines(); // 右欄字幕逐句清單

    applyZoomWidth();
    drawWaveform();
    render();

    // ── CDP 走查掛鉤（丟棄式原型的驗證用，不影響行為）──────────────
    // 目前統計數值
    window.__vtStats = () => ({
      duration: state.duration,
      cutTotal: cutTotal(),
      finalDuration: finalDuration(),
      cutCount: state.cuts.length,
      cuts: state.cuts.map((c) => c.slice()),
      origText: $("vt-stat-orig").textContent,
      finalText: $("vt-stat-final").textContent,
    });
    // 程式化加一段剪除（截圖用；等同拖曳框選）
    window.__vtAddCut = (a, b) => {
      const ok = addCut(a, b);
      render();
      return ok;
    };
    // 目前字幕狀態（每張卡的 kept/cut/partial）
    window.__vtSubs = () =>
      state.subs.map((s) => ({
        start: s.start,
        end: s.end,
        text: s.text,
        status: subStatus(s),
      }));
    // 就地編輯／斷句／合併的程式化入口（走查用，行為與鍵盤、小鈕相同）
    window.__vtStartEdit = (i, caret) => {
      startEditSub(i, caret);
      return state.editingSub;
    };
    window.__vtEditBox = () => {
      const row = document
        .getElementById("vt-line-list")
        .querySelector(".vt-line:focus-within");
      if (!row) return null;
      const inp = row.querySelector(".vt-line-input");
      return {
        index: Number(row.dataset.i),
        value: inp.value,
        focused: document.activeElement === inp,
        caret: inp.selectionStart,
        tools: Array.from(row.querySelectorAll(".vt-line-tool")).map((b) => ({
          act: b.dataset.act,
          label: b.getAttribute("aria-label"),
          disabled: b.disabled,
        })),
      };
    };
    // 右欄清單的實際渲染結果（驗證直式清單與時間軸同步）
    window.__vtLines = () =>
      Array.from(document.querySelectorAll("#vt-line-list .vt-line")).map(
        (n) => ({
          time: n.querySelector(".vt-line-time").textContent,
          text: n.querySelector(".vt-line-input").value,
          cut: n.classList.contains("is-cut"),
          partial: n.classList.contains("is-partial"),
        }),
      );
    // 目前樣式 state 快照
    window.__vtSide = () =>
      Array.from(document.querySelectorAll(".vt-side-sec")).map((sec) => {
        const h = sec.querySelector(":scope > .vt-side-head");
        const b = sec.querySelector(":scope > .vt-side-body");
        return {
          sec: sec.dataset.sec || null,
          title: sec.querySelector(".vt-side-title").textContent,
          expanded: h.getAttribute("aria-expanded"),
          caret: sec.querySelector(".vt-side-caret").textContent,
          bodyShown: b ? getComputedStyle(b).display !== "none" : null,
          bodyH: b ? Math.round(b.getBoundingClientRect().height) : null,
          secH: Math.round(sec.getBoundingClientRect().height),
        };
      });
    window.__vtStyle = () => Object.assign({}, state.style);
    // 目前預覽字幕的實際 computed 樣式（反向驗證綁定用）
    window.__vtPreview = () => {
      const el = $("vt-sub-preview-text");
      if (!el) return null;
      const cs = getComputedStyle(el);
      return {
        text: el.textContent,
        fontFamily: cs.fontFamily,
        fontSize: cs.fontSize,
        fontWeight: cs.fontWeight,
        color: cs.color,
        background: cs.backgroundColor,
        textShadow: cs.textShadow,
        hasBox:
          el.style.background !== "transparent" && el.style.background !== "",
      };
    };
    // 程式化改樣式（截圖用；等同操作面板控制項）
    window.__vtSetStyle = (patch) => {
      Object.assign(state.style, patch || {});
      applyStyle();
      return Object.assign({}, state.style);
    };
    // 目前所有標題卡
    window.__vtCards = () => state.titleCards.map((c) => Object.assign({}, c));
    // 從第 i 句字幕升格成標題卡（等同點卡片上的 ⤴）
    window.__vtPromote = (i) => {
      const c = addCardFromSub(i);
      return c ? Object.assign({}, c) : null;
    };
    // 拖版型到軌道的程式化版本（等同 drag 版型縮圖放到標題卡軌）
    window.__vtDropTpl = (tpl, t) => {
      if (!CARD_TEMPLATES[tpl]) return null;
      const c = addCard(
        makeCard(t, Math.min(state.duration, t + CARD_DEFAULT_DUR), "", tpl),
      );
      return Object.assign({}, c);
    };
    // 改選取中標題卡的大小／位置（等同滑桿與直接拖）
    window.__vtSetCard = (patch) => {
      const c = findCard(state.selectedCard);
      if (!c) return null;
      Object.assign(c, patch || {});
      rememberCard(c);
      renderCardTrack();
      syncCardInspector();
      updateCardLayer(true);
      return Object.assign({}, c);
    };
    window.__vtSeek = (t) => {
      seekTo(t);
      return $("vt-video").currentTime;
    };
    window.__vtSelectCard = (id) => {
      selectCard(id);
      return state.selectedCard;
    };
    // 疊層上實際渲染出來的標題卡（反向驗證版型與字級）
    window.__vtCardView = () => {
      const el = document.querySelector("#vt-card-layer .vt-card-view");
      if (!el) return null;
      const cs = getComputedStyle(el);
      const layer = $("vt-card-layer");
      return {
        text: el.textContent,
        cls: el.className,
        fontSize: cs.fontSize,
        fontWeight: cs.fontWeight,
        left: el.style.left,
        top: el.style.top,
        layerW: layer.offsetWidth,
        layerH: layer.offsetHeight,
      };
    };
    // 疊層上所有卡（重疊時會有多張，DOM 順序＝上下層）
    window.__vtCardViews = () =>
      Array.from(document.querySelectorAll("#vt-card-layer .vt-card-view")).map(
        (el) => ({
          id: Number(el.dataset.id),
          text: el.textContent,
          cls: el.className,
          selected: el.classList.contains("is-selected"),
          z: getComputedStyle(el).zIndex,
        }),
      );
    // 軌道上的標題卡塊數量與位置（驗證第三軌真的長出來）
    // 鍵盤進出點與它圈出來的範圍
    window.__vtMark = () => ({
      in: state.mark.in,
      out: state.mark.out,
      range: markRange(),
    });
    // 時間軸上那塊選區的實際樣子（相對時間軸寬度的 0–1）
    window.__vtSelectionView = () => {
      const el = document.querySelector("#vt-timeline .vt-selection");
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const tl = document.getElementById("vt-timeline").getBoundingClientRect();
      return {
        left: r.x - tl.x < 0 ? 0 : (r.x - tl.x) / tl.width,
        width: r.width / tl.width,
        mark: el.classList.contains("is-mark"),
        text: el.textContent,
      };
    };
    // 目前的剪輯指令（＝按下「輸出剪輯指令」會看到的那份）
    window.__vtPlan = () => buildPlan();
    // 匯入一份剪輯指令；回傳 {ok, ...} 或 {ok:false, error}
    window.__vtApplyPlan = (text) => {
      try {
        const obj = typeof text === "string" ? JSON.parse(text) : text;
        return Object.assign({ ok: true }, applyPlan(obj));
      } catch (err) {
        return { ok: false, error: err.message };
      }
    };
    // 面板狀態：開著沒、摘要文字、textarea 內容長度、錯誤訊息
    window.__vtPlanDialog = () => ({
      open: !document.getElementById("vt-plan").hidden,
      summary: document.getElementById("vt-plan-sum").textContent,
      len: document.getElementById("vt-plan-json").value.length,
      msg: document.getElementById("vt-plan-msg").textContent,
      // 送出的四態全靠這三個欄位斷言：busy 鎖鈕、is-ok 成功、無 class 失敗
      msgKind: document.getElementById("vt-plan-msg").className,
      busy: document.getElementById("vt-plan-push").disabled,
    });
    window.__vtPlanPush = (render) => pushPlan(!!render);
    // 合成進度區的可見狀態，四態走查靠這些欄位斷言
    window.__vtRender = () => {
      const box = document.getElementById("vt-render");
      // 讀「人看不看得到」而不是 hidden 屬性：屬性設了但被 CSS 的 display 蓋掉時，
      // 讀屬性會回報已收起、畫面上卻還在。走查要測的是後者。
      const 看得見 = (id) => {
        const e = document.getElementById(id);
        if (!e) return false;
        const r = e.getBoundingClientRect();
        return (
          getComputedStyle(e).display !== "none" && r.width > 0 && r.height > 0
        );
      };
      return {
        hidden: !看得見("vt-render"),
        kind: box.className,
        text: document.getElementById("vt-render-text").textContent,
        percent: document.getElementById("vt-render-fill").style.width,
        cancelShown: 看得見("vt-render-cancel"),
        revealShown: 看得見("vt-render-reveal"),
        closeShown: 看得見("vt-render-close"),
        exportDisabled: document.getElementById("vt-export").disabled,
        previewDisabled: document.getElementById("vt-preview").disabled,
        targets: readTargets(),
        polling: renderTimer !== null,
      };
    };
    window.__vtCardTrack = () =>
      Array.from(document.querySelectorAll("#vt-card-track .vt-card")).map(
        (n) => ({
          text: n.querySelector(".vt-card-txt").textContent,
          badge: n.querySelector(".vt-card-badge").textContent,
          left: n.style.left,
          width: n.style.width,
          top: n.style.top, // 自動車道分配到的垂直位置（px）
          height: n.style.height,
          selected: n.classList.contains("is-selected"),
          // 與 __vtLines 對稱：卡落剪除／裁切區會被灰掉，走查靠這兩個布林斷言
          cut: n.classList.contains("is-cut"),
          partial: n.classList.contains("is-partial"),
        }),
      );
    // 標題卡軌整體高度（px）：車道數變多時應該長高，只有 1 車道要等於原本的 44px
    window.__vtCardTrackHeight = () => $("vt-card-track").offsetHeight;

    // ── 對齊面板走查掛鉤 ──────────────────────────────────────────────
    // 五個對齊欄位的 state 值 + 目前 totalShift + 四態 + DOM 輸入實況；round-trip 走查靠這些斷言
    window.__vtAlign = () => ({
      audioPath: state.audioPath,
      audioSyncOffset: state.audioSyncOffset,
      camSyncOffsetB: state.camSyncOffsetB,
      headTrimSec: state.headTrimSec,
      tailTrimSec: state.tailTrimSec,
      subtitleOffsetSec: state.subtitleOffsetSec,
      episodeDir: state.episodeDir,
      totalShift: alignShift(),
      state: state.align.state,
      err: state.align.err,
      inputs: {
        audio: $("vt-al-audio").value,
        camb: $("vt-al-camb").value,
        head: $("vt-al-head").value,
        tail: $("vt-al-tail").value,
        sub: $("vt-al-sub").value,
        audioDisabled: $("vt-al-audio").disabled,
        saveDisabled: $("vt-al-save").disabled,
      },
      statusText: $("vt-al-status").textContent,
      statusKind: $("vt-al-status").className,
    });
    // toDiskTime 的程式化入口（存檔逆運算走查用）
    window.__vtToDisk = (t) => toDiskTime(t);
    // 程式化設定對齊欄位（等同在輸入框打字 + change）；回傳新的 totalShift
    window.__vtSetAlign = (patch) => {
      if (patch && typeof patch === "object") {
        Object.assign(state, patch);
        syncAlignInputs();
      }
      return alignShift();
    };
    // 程式化按「儲存對齊」，回傳 promise（走查用 await）
    window.__vtSaveAlign = () => saveAlignment();
  }

  bindEvents();
  init();
})();
