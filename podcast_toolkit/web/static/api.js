// 後端傳輸層：跟 /api/* 講話的 fetch、送出前的 payload 序列化、存檔的序列化通道。
//
// 邊界：這裡只管「怎麼把資料送出去 / 拿回來」。拿回來的東西怎麼灌進 state、
// 灌完要重畫什麼，全部留在 app.js —— 那是狀態與渲染，不是傳輸。
// 所以 loadEpisodeState 只有開頭那段 fetch 搬過來（apiGetEpisode），
// 後面近 190 行的欄位對照留在原地；整支搬過來會把二十幾個 render* 拖過邊界，
// 那就不是「api」模組了。
//
// 循環 import（同 timeline.js）：本檔匯入 app.js 的 state，app.js 也匯入本檔。
// 安全條件是**匯入的符號只在函式執行時用，不在模組頂層求值時用** —— 否則 app.js
// 還沒評估完就會踩到 TDZ。本檔只匯入 state，且只在函式體內讀，符合條件。
import { expandedCards, state } from "./app.js";

// 卡 key ↔ 時間區間的單一換算來源（與影片模式共用，見 timeline-core.js）
import { alignShift, cardKeysToCuts } from "./timeline-core.js";

// 取集狀態。只負責傳輸與狀態碼分類，回傳原始 JSON（snake_case 欄位）。
// cache:"no-store"：保險再加一層，避免瀏覽器吃舊 cache → 存檔後重載拿到存檔前資料
// （後端 /api/episode 也補了 no-store header；雙保險）
export async function apiGetEpisode() {
  const r = await fetch("/api/episode", { cache: "no-store" });
  if (r.status === 409) {
    // 後端尚未選集（重啟 / 多分頁 / 直接打 /edit URL）→ 回 dashboard 重選
    window.location.href = "/";
    throw new Error("尚未選集，導回 dashboard");
  }
  if (!r.ok) throw new Error(`/api/episode HTTP ${r.status}`);
  return r.json();
}

// 寫回 yaml 用：base + 可選 .b override 合成單一 dict；base null → null
function serializeCropForSave(base, b) {
  if (!base) return null;
  return b ? { ...base, b: { ...b } } : { ...base };
}

// 時間軸還原：把畫面上的時間還原成磁碟 _v2.srt 的時間，必須是 loadEpisodeState 位移的精確反向
//   載入：display = disk + alignShift(state)
//   存檔：disk = display − alignShift(state)
// 非破壞性字幕偏移是「顯示/合成層」的位移，不該被存進卡片磁碟時間；少減回去 → 拖一張卡存一次就漂一個偏移量。
// 位移算式與 audioPath 守衛只有一份，在 timeline-core.js: alignShift（B3 併軌）。
// 併軌前這裡是另抄的一份且漏了守衛：yaml 留著 audio_sync_offset 但沒接外接音檔時，
// 載入不位移、存檔卻減回去 → 卡時間每存一次漂 sync_offset 秒。
// 位移=0 → 原值回傳（no-op，只剩毫秒取整）。
function _diskOffset() {
  return -alignShift(state);
}
// 取小數 3 位：_v2.srt 的時間本來就是毫秒精度，3 位才能跟後端 _from_idx(deletions) 算出的
// 區間逐位元對齊（取 2 位會差幾毫秒 → 「改存檔格式不改出片」這個保證就破了）。
const _ms = (v) => Math.round(v * 1000) / 1000;

function toDiskTime(t) {
  const off = _diskOffset();
  return { start: _ms(t.start + off), end: _ms(t.end + off) };
}

export function cutToDiskTime([s, e]) {
  const off = _diskOffset();
  return [_ms(s + off), _ms(e + off)];
}

export function cutFromDiskTime([s, e]) {
  const off = _diskOffset();
  return [_ms(s - off), _ms(e - off)];
}

// 所有 /api/save 共用的序列化通道：主儲存鈕、cam modal 儲存、一鍵對齊 auto-save
// 三條路徑可能並發；不序列化的話兩個 POST 交錯，後發先回會互蓋 episode.yaml。
let _saveChain = Promise.resolve();
export function postSave(payload) {
  const run = async () => {
    // 集數儲存隔離：每發帶 episode_dir，後端據此確認目標集，避免多分頁存錯集。
    let r = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, episode_dir: state.episodeDir }),
    });
    if (r.status === 409) {
      // 後端尚未選集（重啟／多分頁）→ 先用本地記得的集路徑重開，再重送一次
      const episodeDir =
        state.episodeDir || localStorage.getItem("edit.episodeDir") || "";
      if (episodeDir) {
        const reopen = await fetch("/api/episodes/open", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: episodeDir }),
        });
        if (reopen.ok) {
          r = await fetch("/api/save", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...payload, episode_dir: state.episodeDir }),
          });
        }
      }
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r;
  };
  // 不管前一發成敗都接著跑；回傳的 promise 保留各呼叫端自己的錯誤處理
  const p = _saveChain.then(run, run);
  _saveChain = p.catch(() => {});
  return p;
}

// 主儲存鈕與「合成設定 → 開始合成」共用的 /api/save payload 序列化。
// 抽出來讓「合成的下一步」能用同一條已驗證的存檔路徑把倍速等輸出設定寫進 episode.yaml。
export function buildSavePayload({ withSpeed = false } = {}) {
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
    // ── 刪段（B1 單一 source of truth）──
    // 磁碟上只留時間版 cuts：後端 cut_intervals_from_cfg 本來就 cuts 優先，
    // 兩種格式並存時 deletions 會整份靜默失效（影片模式存過的集就是這樣）。
    // 這裡把 UI 的卡 key 選取換成「每張被刪卡一段」的區間——刻意不預先合併，
    // 才跟後端舊路徑 _from_idx(deletions) 的輸出逐位元相同（cut_pad=0 時不合併）。
    // deletions 一律送空：告訴後端「這次存檔帶了刪段，結果沒有 idx 版」→ 完成遷移。
    // 排除 new: 開頭的鍵（剛加的新字卡）——舊路徑 _translate 本來就解不掉它、等於沒刪，
    // 改走 cuts 會突然真的剪掉那段聲音；行為差異不在本梯範圍，維持原樣。
    // B3-3 重查結論：這個 filter 是**防禦性**的，實際永遠過濾不到東西 —— 新增卡在三個上游
    // 入口都被擋掉（renderCards 的 newCard continue、marquee 的 startsWith 過濾、載入時
    // newCards 先清空）。前提由 tests/test_new_card_keys_never_deleted.py 釘住：哪天被改破，
    // 那支測試先紅，這裡才是真的最後一道關。
    // state.foreignCuts 是載入時換不回卡 key 的段（影片模式在句中／跨停頓剪的）：
    // 本編輯器不動它，但一定要原樣送回去，否則存一次檔就把別人剪的段吃掉。
    cuts: [
      ...cardKeysToCuts(
        [...state.deletions].filter((k) => !String(k).startsWith("new:")),
        expandedCards(),
      ),
      ...(state.foreignCuts || []),
    ]
      .map(cutToDiskTime)
      .sort((a, b) => a[0] - b[0]),
    deletions: [],
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
    // ── 時間軸還原 ──
    // 載入時（loadEpisodeState）字卡被整批 -audioSyncOffset 移到 cam A 軸（讓播放預覽的字幕
    // highlight 對得上 video.currentTime）。但磁碟 _v2.srt 是「外接音檔時間軸」，存檔端
    // 必須把使用者在 cam A 軸上微調出來的 start/end 加回 +audioSyncOffset 還原成外接音檔軸，
    // 才能跟磁碟一致、避免每次「存→重載」都再被減一次 offset（症狀：時間越存越早、修不回）。
    // 新增字卡：[{start, end, text}]；後端 append 進 SRT、依時間排序重編號
    // 新卡 start/end 來自 video.currentTime（cam A 軸），同樣要 +audioSyncOffset 還原成磁碟軸
    new_cards: state.newCards.map((c) => ({
      ...toDiskTime(c),
      text: c.text,
    })),
    // 字幕時間覆寫：composite key（int 整卡 / "idx:part" 子卡）→ {start, end}；
    // 時間軸拖拉與 ⏱ 工具列共用這一份。同 +audioSyncOffset 還原磁碟軸。
    // （舊版另有 time_overrides payload，後端仍相容接受，但前端已不再送。）
    card_timings: Object.fromEntries(
      [...state.cardTimings.entries()].map(([k, t]) => [k, toDiskTime(t)]),
    ),
    // Reels 片段：list of {name, start_card, end_card}；空 list 後端會把 key 砍掉
    reels_clips: state.reelsClips.map((c) => ({
      name: c.name,
      start_card: c.start_card,
      end_card: c.end_card,
    })),
    // 標題卡（B2）：純文字卡只送 {id,start,end,text}，tpl 由後端補預設。
    // 時間採「逐字原樣」round-trip（不走 toDiskTime）——標題卡是顯示層時間、非磁碟 _v2.srt 卡，
    //   若也做 offset 位移，載入時已加 totalShift、存檔又減一次基準不同會漂；原樣存讀保證精準往返。
    // plan_io（/api/apply-plan）定位卡的 scale/x/y/tpl 於載入時已保留在 state，這裡原樣透傳，
    //   避免本存檔鏈把後端既有定位卡清成純文字卡（後端 save_state 對帶入的合法值會保留）。
    // 純文字卡本身沒有 scale/x/y → 自然只送 4 欄，不會誤帶定位資訊。
    title_cards: state.titleCards
      .filter((c) => c && c.end > c.start && String(c.text || "").trim())
      .map((c) => {
        const out = { id: c.id, start: c.start, end: c.end, text: c.text };
        if (c.tpl != null) out.tpl = c.tpl;
        for (const k of ["scale", "x", "y"]) {
          if (c[k] != null) out[k] = c[k];
        }
        return out;
      }),
    // 版面模式（B4）：控標題卡軌顯隱。後端存檔鍵 layout_mode（"podcast"|"video"，
    //   壞值回退 podcast）。比照 title_cards 隨主存檔往返，round-trip 由 load_state 讀回。
    layout_mode: state.layoutMode === "video" ? "video" : "podcast",
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
