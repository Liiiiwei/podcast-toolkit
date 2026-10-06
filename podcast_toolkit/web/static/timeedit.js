// ⏱ 時間編輯工具列子系統：字幕卡「調時間」的那一整套 —— 目標抽象（既有卡／新卡）、
// 循環試聽、重疊警示、工具列 DOM、鍵盤微調與打點。
//
// 邊界（D6 第三刀，2026-09-26 抽出，見 docs/plans/2026-09-25-app-js-split-render.md）：
//   該進來：只服務「⏱ 工具列」這一個互動的狀態與 DOM。
//   不該進來：renderCards（整列渲染的中樞，搬它要 29 個 export，已量化否決）、
//   renderNewCardRow（它只在最後兩行碰到本檔）、以及任何改字幕文字／存檔的動作。
//   為什麼另開新檔而不併進 render.js：這裡九成不是渲染而是互動狀態機
//   （循環試聽 listener、document 層 keydown、undo 連發合併），塞進 render.js
//   會讓那個檔變成第二個 app.js。
//
// 與 app.js 的耦合面（刻意只有這幾支，要再加一支之前先想清楚值不值得）：
//   讀狀態：state、expandedCards、getEffectiveCardTime
//   改狀態：pushUndo、setCardTime、clearCardTimings、setUndoCoalesce、clearAudition
//   後兩支是 setter 而不是變數：ESM 的 import 是唯讀繫結，跨檔對 app.js 的 let
//   賦值會直接 TypeError，不能照搬原本的 `_undoCoalesce = ...`。
//   反向同理 —— app.js 原本三處 `_timeEditCtl = null; stopTimeLoop();` 收成本檔的
//   resetTimeEdit()。
//
// 循環 import 的鐵律（與 render.js／timeline.js／api.js 同一形狀）：
//   app.js 是進入點，本檔求值時它的 const/let 還在 TDZ。
//   **本檔模組求值階段（top-level）絕不可呼叫或讀取任何從 app.js 匯入的東西**，
//   只能在函式體內（被呼叫時）讀。違反的症狀是開頁即 ReferenceError，整個編輯器空白。
//   本檔唯一的 top-level 副作用是 TIME_NUDGE_STEPS 那段的
//   document.addEventListener("keydown", onTimeNudgeKey)：它只掛本檔自己的函式、
//   不讀任何 import，所以安全。（掛載時機因此比原本早，但既有 keydown 監聽沒有一個
//   用 stopImmediatePropagation，先後順序不影響行為。）
import {
  $,
  clearAudition,
  clearCardTimings,
  expandedCards,
  fmtTimeCard,
  getEffectiveCardTime,
  pushUndo,
  setCardTime,
  setUndoCoalesce,
  state,
} from "./app.js";

// 卡面時間／時間軸區塊／字幕預覽是同一份狀態的三個讀者，repaint 要一次同步三邊
import { syncTimelineBlock } from "./timeline.js";
import { renderCaption, renderTopbar } from "./render.js";

// 工具列底部提示列的文案來源（與快捷鍵 modal 同一張表）
import { timeToolbarHintHtml } from "./shortcuts.js";

// "12.34" / "1:23.45" / "01:23" → 秒；解析不出來回 null（呼叫端負責維持原值）
function parseTimeCard(str) {
  const raw = String(str == null ? "" : str).trim();
  if (!raw) return null;
  const m = raw.match(/^(-)?(?:(\d+):)?(\d+(?:\.\d+)?)$/);
  if (!m) return null;
  const sec = (m[2] ? parseInt(m[2], 10) * 60 : 0) + parseFloat(m[3]);
  if (!isFinite(sec)) return null;
  return m[1] ? -sec : sec;
}

// 時間微調的「目標」抽象：既有卡走 cardTimings（與時間軸拖拉同一份），新卡直接改自身 start/end。
// target = { domKey, get()->{start,end}, set(s,e), reset|null, isDirty()->bool }
export function cardTimeTarget(c) {
  return {
    domKey: String(c.idx),
    get: () => getEffectiveCardTime(c),
    set: (s, e) => setCardTime(c, s, e),
    reset: () => {
      if (!state.cardTimings.has(c.idx)) return;
      pushUndo();
      // 整卡「還原」連子卡的釘死時間一起清（否則封套回原位、子卡還停在舊處）
      clearCardTimings(c.idx);
    },
    isDirty: () => state.cardTimings.has(c.idx),
  };
}
export function newCardTimeTarget(nc) {
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

// 目前展開的 ⏱ 工具列控制器 { target, repaint }。給鍵盤微調（onTimeNudgeKey）用；
// 工具列關掉 / 整列重繪換卡時會被覆寫或清成 null。
export let _timeEditCtl = null;

// === E1 循環試聽 ===
// 工具列開啟期間把播放圈在「起點 −0.3s ～ 訖點 ＋0.3s」。邊界每圈從 _timeEditCtl.target
// 重讀生效值（既有卡走 getEffectiveCardTime、新卡讀 nc 自身），所以打字／±鈕／方向鍵／
// ⇤⇥／[ ]／時間軸拖同卡邊緣改值後，下一圈自然生效 —— 不另存循環邊界 state。
// listener 只在工具列開啟期間掛在 #video 上；關工具列（Esc／再點 ⏱／換集）即拆，全域無殘留。
const TIME_LOOP_PAD = 0.3;
export let _teLoopOn = false; // 循環開關（toggle 鈕／P 鍵可停續；工具列關閉時恆為 false）
let _teLoopAttached = false; // timeupdate listener 是否掛著（開工具列期間恆掛）
function onTimeLoopTick() {
  // 工具列已關卻還在跑（某個關閉路徑漏拆時的自癒）→ 拆 listener 收尾
  if (!_timeEditCtl || state.timeEditKey === null) {
    stopTimeLoop(false);
    return;
  }
  const v = $("#video");
  // 暫停中不拉回：讓「暫停 → 拖到目標點 → ⇤⇥／[ ] 打點」的工作流可以自由移動游標
  if (!_teLoopOn || v.paused) return;
  const t = _timeEditCtl.target.get();
  if (v.currentTime > t.end + TIME_LOOP_PAD) {
    v.currentTime = Math.max(0, t.start - TIME_LOOP_PAD);
  }
}
function startTimeLoop() {
  if (!_timeEditCtl) return;
  if (!_teLoopAttached) {
    $("#video").addEventListener("timeupdate", onTimeLoopTick);
    _teLoopAttached = true;
  }
  _teLoopOn = true;
  clearAudition(); // 循環接管播放守門，取消單次試聽（P 鍵此時也不會再進 auditionCard）
  const v = $("#video");
  const t = _timeEditCtl.target.get();
  v.currentTime = Math.max(0, t.start - TIME_LOOP_PAD);
  v.play().catch(() => {});
  syncTimeLoopBtn();
}
export function stopTimeLoop(pauseVideo = true) {
  if (_teLoopAttached) {
    $("#video").removeEventListener("timeupdate", onTimeLoopTick);
    _teLoopAttached = false;
  }
  // 循環驅動中的播放才順手暫停（「影片停在當下即可」）；使用者自己在放的不動
  if (_teLoopOn && pauseVideo) $("#video").pause();
  _teLoopOn = false;
  syncTimeLoopBtn();
}
// toggle 鈕／P 鍵：停 = 只關循環判定並暫停（listener 留著，工具列還開）；續 = 從頭起圈
export function toggleTimeLoop() {
  if (_teLoopOn) {
    _teLoopOn = false;
    $("#video").pause();
    syncTimeLoopBtn();
  } else {
    startTimeLoop();
  }
}
// 把工具列裡的「循環」鈕同步到 _teLoopOn（鈕可能不在 DOM，例如整列重繪的瞬間）
function syncTimeLoopBtn() {
  const b = document.querySelector(".card-time-edit .te-loop");
  if (!b) return;
  b.setAttribute("aria-pressed", _teLoopOn ? "true" : "false");
  b.classList.toggle("active", _teLoopOn);
}

// E3：本卡（生效起訖 t）與前後相鄰既有卡的重疊警示文字。維持「刻意不夾制」——
// 重疊照樣放行，只在工具列給可見訊號。相鄰以生效 start 排序（expandedCards 已排序），
// 只看緊鄰前一張與後一張；已刪卡不進最終輸出、新卡沒有 #N 可指，都不比。
// 顯示格式一位小數（X.Xs）：低於 0.05s 的重疊顯示會變 0.0s，視為貼齊不警示
// （字幕卡半數首尾貼齊，0 距離是常態不是問題）。
function timeOverlapWarnings(domKey, t) {
  const rows = expandedCards().filter(
    (r) => r.c && String(r.key) !== domKey && !state.deletions.has(r.key),
  );
  let prev = null;
  let next = null;
  for (const r of rows) {
    if (r.start <= t.start) prev = r;
    else {
      next = r;
      break;
    }
  }
  const out = [];
  if (prev) {
    const ov = Math.min(prev.end, t.end) - Math.max(prev.start, t.start);
    if (ov >= 0.05) out.push(`⚠ 與 #${prev.c.idx} 重疊 ${ov.toFixed(1)}s`);
  }
  if (next) {
    const ov = Math.min(t.end, next.end) - Math.max(t.start, next.start);
    if (ov >= 0.05) out.push(`⚠ 與 #${next.c.idx} 重疊 ${ov.toFixed(1)}s`);
  }
  return out;
}

// 時間微調工具列。按鈕只做 targeted DOM 更新，不整列 renderCards、不跳 scroll；
// undo / 整列重繪時靠 renderCards 的注入點還原。
export function buildTimeToolbar(target) {
  const bar = document.createElement("div");
  bar.className = "card-time-edit";
  bar.addEventListener("click", (e) => e.stopPropagation());
  const val = document.createElement("span");
  val.className = "te-val";
  // E3：重疊警示（時長旁的紅字，repaint 時即時增減）
  const warn = document.createElement("span");
  warn.className = "te-overlap";
  warn.hidden = true;
  const inS = mkTimeInput("起點時間（可打 12.34 或 1:23.45）");
  const inE = mkTimeInput("終點時間（可打 12.34 或 1:23.45）");
  const repaint = () => {
    const t = target.get();
    inS.value = fmtTimeCard(t.start);
    inE.value = fmtTimeCard(t.end);
    val.textContent = `${(t.end - t.start).toFixed(2)}s`;
    const msgs = timeOverlapWarnings(target.domKey, t);
    warn.textContent = msgs.join("｜");
    warn.hidden = msgs.length === 0;
    const card = document.querySelector(
      `#cards-list .card[data-idx="${target.domKey}"]`,
    );
    if (card) {
      card.classList.toggle("time-dirty", target.isDirty());
      const cv = card.querySelector(".card-time-val");
      // 卡號那行是整列渲染時貼的，這裡只換時間兩行 —— 整段覆寫會把 #34 洗掉，
      // 一開工具列卡號就消失、關掉才回來（既有缺陷，順手在同一行修掉）。
      if (cv) {
        const head = cv.textContent.split("\n")[0];
        const keep = head.startsWith("#") ? `${head}\n` : "";
        cv.textContent = `${keep}${fmtTimeCard(t.start)}\n${fmtTimeCard(t.end)}`;
      }
    }
    // 時間軸也要跟上：卡面數字、時間軸區塊、播放器 seek 是同一份狀態的三個讀者，
    // 少同步一個就會出現「卡片說 3:10.6、點時間軸卻跳到 3:10.1」這種對不起來的畫面。
    syncTimelineBlock(target.domKey, t.start, t.end, target.isDirty());
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
  // 打字送出：解析失敗（或空白）就退回目前值，不動資料
  const commit = (inp, which) => () => {
    const v = parseTimeCard(inp.value);
    const t = target.get();
    if (v === null) {
      repaint();
      return;
    }
    if (which === "start") target.set(v, t.end);
    else target.set(t.start, v);
    repaint();
  };
  for (const [inp, which] of [
    [inS, "start"],
    [inE, "end"],
  ]) {
    const done = commit(inp, which);
    inp.addEventListener("blur", done);
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        done();
      } else if (e.key === "Escape") {
        e.preventDefault();
        repaint();
        inp.blur();
      }
    });
  }
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
  // E1：循環試聽 toggle。開工具列時 toggleTimeEdit 會自動 startTimeLoop，
  // 這裡建鈕時帶當下狀態（整列重繪重建工具列時循環不中斷，鈕的亮暗要跟上）。
  const loopBtn = mk("循環", "循環試聽這張卡（P 鍵同效）", () =>
    toggleTimeLoop(),
  );
  loopBtn.classList.add("te-loop");
  loopBtn.setAttribute("aria-pressed", _teLoopOn ? "true" : "false");
  loopBtn.classList.toggle("active", _teLoopOn);
  bar.append(
    lab("起"),
    inS,
    mk("⇤游標", "把起點設到目前播放位置", () => {
      const t = target.get();
      target.set($("#video").currentTime, t.end);
    }),
    mk("−", "起點 −0.1s（←）", () => nudge(-0.1, 0)),
    mk("＋", "起點 +0.1s（→）", () => nudge(0.1, 0)),
    lab("訖"),
    inE,
    mk("游標⇥", "把終點設到目前播放位置", () => {
      const t = target.get();
      target.set(t.start, $("#video").currentTime);
    }),
    mk("−", "終點 −0.1s（⌥←）", () => nudge(0, -0.1)),
    mk("＋", "終點 +0.1s（⌥→）", () => nudge(0, 0.1)),
    loopBtn,
    val,
    warn,
  );
  if (target.reset) {
    bar.append(mk("還原", "清除這張卡的時間微調", () => target.reset()));
  }
  // E2：快捷鍵提示列 —— 微調快捷鍵早就存在但介面零提示等於不存在，讓它們現形
  const hints = document.createElement("div");
  hints.className = "te-hints";
  hints.innerHTML = timeToolbarHintHtml();
  bar.append(hints);
  repaint();
  _timeEditCtl = { target, repaint };
  return bar;
}

function mkTimeInput(title) {
  const inp = document.createElement("input");
  inp.type = "text";
  inp.className = "te-input";
  inp.title = title;
  inp.inputMode = "decimal";
  inp.spellcheck = false;
  inp.autocomplete = "off";
  return inp;
}

// 鍵盤微調：←/→ 0.1s、Shift 0.5s、⌘ 1.0s（沿用 trim 拖把既有慣例）；加 ⌥ 改調終點。
// 獨立於「編輯工作流快捷鍵」那個 listener —— 那個開頭就 return 掉所有修飾鍵，
// 而這裡的 ⌘/⌥ 組合正是主力用法。
const TIME_NUDGE_STEPS = { plain: 0.1, shift: 0.5, meta: 1.0 };
document.addEventListener("keydown", onTimeNudgeKey);
function onTimeNudgeKey(e) {
  if (!_timeEditCtl || state.timeEditKey === null) return;
  const isArrow = e.key === "ArrowLeft" || e.key === "ArrowRight";
  const isBracket = e.key === "[" || e.key === "]";
  if (!isArrow && !isBracket && e.key !== "Escape") return;
  if (e.ctrlKey) return;
  // [ ] 打點與 Esc 關工具列都是無修飾鍵語意（⌘[ 這類瀏覽器組合不搶）
  if (!isArrow && (e.metaKey || e.altKey || e.shiftKey)) return;
  const t = e.target;
  // 焦點在輸入框／字幕本文時讓出原生游標移動
  if (
    t &&
    (t.tagName === "INPUT" ||
      t.tagName === "TEXTAREA" ||
      t.isContentEditable === true)
  ) {
    return;
  }
  // 焦點在自己就吃 ←/→ 的控制項（片頭/片尾裁切拖把 role=slider）時整個讓開。
  // 這是 document 層的監聽，不讓開的話一次按鍵會同時改裁切點和字卡時間 —— 使用者
  // 以為在微調片頭，字幕卡的時間也被偷偷改掉了。
  if (t && t.closest && t.closest('[role="slider"]')) return;
  if (document.querySelector("dialog[open]")) return;
  // 工具列已不在畫面上（卡被刪 / 整列重繪沒重建）→ 別對看不見的東西改時間
  if (
    !document.querySelector(
      `#cards-list .card[data-idx="${state.timeEditKey}"] .card-time-edit`,
    )
  ) {
    return;
  }
  if (e.key === "Escape") {
    // E1：Esc 關工具列（循環一併停止）。輸入框內的 Esc 到不了這裡（上面焦點守衛讓開，
    // 由輸入框自己還原值＋blur）—— 所以是「第一下還原輸入、第二下關工具列」。
    e.preventDefault();
    closeTimeEdit();
    return;
  }
  e.preventDefault();
  if (isBracket) {
    // E2：單鍵打點 —— [ 設起點、] 設訖點＝目前播放位置（與 ⇤/⇥ 鈕同路徑同效）
    const cur = _timeEditCtl.target.get();
    const now = $("#video").currentTime;
    if (e.key === "[") _timeEditCtl.target.set(now, cur.end);
    else _timeEditCtl.target.set(cur.start, now);
    _timeEditCtl.repaint();
    return;
  }
  const step = e.metaKey
    ? TIME_NUDGE_STEPS.meta
    : e.shiftKey
      ? TIME_NUDGE_STEPS.shift
      : TIME_NUDGE_STEPS.plain;
  const d = e.key === "ArrowLeft" ? -step : step;
  const cur = _timeEditCtl.target.get();
  // 長按連發整串算一次編輯：⌘Z 一下就回到按下去之前
  setUndoCoalesce(e.repeat === true);
  try {
    if (e.altKey) _timeEditCtl.target.set(cur.start, cur.end + d);
    else _timeEditCtl.target.set(cur.start + d, cur.end);
  } finally {
    setUndoCoalesce(false);
  }
  _timeEditCtl.repaint();
}

// 開 / 關某卡的時間微調工具列（一次只開一張）
export function toggleTimeEdit(target, cardEl) {
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
    _timeEditCtl = null;
    stopTimeLoop(); // E1：再點 ⏱ 關工具列 → 停循環
  } else {
    cardEl.appendChild(buildTimeToolbar(target));
    cardEl.classList.add("editing-time");
    state.timeEditKey = target.domKey;
    startTimeLoop(); // E1：開工具列（含切到別卡）即自動循環試聽該卡區間
  }
}

// Esc（或任何程式路徑）直接關掉目前開著的時間工具列；循環一併停止
function closeTimeEdit() {
  if (state.timeEditKey === null) return;
  const card = document.querySelector(
    `#cards-list .card[data-idx="${state.timeEditKey}"]`,
  );
  if (card) {
    const bar = card.querySelector(".card-time-edit");
    if (bar) bar.remove();
    card.classList.remove("editing-time");
  }
  state.timeEditKey = null;
  _timeEditCtl = null;
  stopTimeLoop();
}

// 外部把工具列狀態歸零的單一入口（刪掉正在編輯的新卡／換集／插卡）：清控制器＋停循環。
// 抽檔前 app.js 三處各寫一次 `_timeEditCtl = null; stopTimeLoop();`，行為逐字相同。
export function resetTimeEdit() {
  _timeEditCtl = null;
  stopTimeLoop();
}
