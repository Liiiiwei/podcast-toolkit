"""podcast_toolkit/mic_diarize.py — 本地分軌能量分講者管線。

文字來自混音的逐字轉錄（words.json）；分軌 mic wav 只用來算 RMS 判斷每個字是誰講的。
換人處天然切卡，保證輸出無跨講者卡。

設計原則：純函式（易單測）+ orchestrator run()。所有時間單位秒。
"""
from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# 型別別名
Word = dict          # 至少含 w:str, start:float, end:float
Card = dict          # {"start", "end", "text", "speaker", "word_span":(i0,i1)}

NEG_INF: float = float("-inf")


# ──────────────────────────────────────────────
# §1.4 參數物件
# ──────────────────────────────────────────────

@dataclass
class DiarizeParams:
    frame_ms: int = 20
    margin_db: float = 3.0
    silence_floor_db: float = -45.0
    min_turn_sec: float = 0.6
    min_turn_words: int = 2
    hysteresis_db: float = 1.5
    # 切卡（對齊 defaults.yaml resegment 值域）
    maxlen: int = 17
    hardlen: int = 23
    gapmax: float = 0.6
    qend_chars: str = "嗎呢"
    reaction_words: tuple = field(default_factory=lambda: (
        "對", "對啊", "對對", "對對對", "嗯", "嗯嗯嗯",
        "哈哈哈", "哈哈", "哇", "喔", "哦", "蛤", "對啦",
        "好", "是吧", "是喔",
    ))
    dangle_endings: tuple = field(default_factory=lambda: (
        "因為", "所以", "但是", "可是", "然後", "而且",
        "或是", "還是", "而是", "就是", "就",
    ))


# ──────────────────────────────────────────────
# §1.2 純函式
# ──────────────────────────────────────────────

def load_words(words_json_path: Path) -> list:
    """讀 words.json，回 words 陣列。容忍頂層直接是 list。"""
    raw = json.loads(Path(words_json_path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    return raw["words"]


def find_words_json(ep) -> Optional[Path]:
    """定位混音逐字檔：ep.dir 下 glob *_字幕_words.json，取 mtime 最新；無則 None。"""
    candidates = list(Path(ep.dir).glob("*_字幕_words.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def compute_rms_envelope(
    samples: np.ndarray,
    sample_rate: int,
    *,
    frame_ms: int = 20,
) -> tuple:
    """
    切 frame_ms 一段，算每幀 normalized RMS（int16 / 32768）。
    沿用 vad_gate.detect_speech_frames 的 reshape+sqrt(mean(f²))，但回連續 RMS 值不布林化。
    回 (rms_per_frame: float32 ndarray, frame_sec: float)。
    """
    frame_samples = int(sample_rate * frame_ms / 1000)
    frame_sec = frame_samples / sample_rate
    n_frames = len(samples) // frame_samples
    if n_frames == 0:
        return np.zeros(0, dtype=np.float32), frame_sec
    usable = samples[: n_frames * frame_samples].astype(np.float32) / 32768.0
    frames = usable.reshape(n_frames, frame_samples)
    rms = np.sqrt(np.mean(frames * frames, axis=1)).astype(np.float32)
    return rms, frame_sec


def rms_db_in_window(
    envelope: np.ndarray,
    frame_sec: float,
    start: float,
    end: float,
    *,
    offset: float = 0.0,
) -> float:
    """
    取 [start+offset, end+offset] 覆蓋到的幀，回該段 RMS 的 dB（20log10）。
    - 視窗不足一幀時：取字中點所在幀（§8 風險①）。
    - 越界：clip 到合法範圍；全空 → 回 NEG_INF。
    """
    n = len(envelope)
    if n == 0:
        return NEG_INF

    t_start = start + offset
    t_end = end + offset

    i0 = int(math.floor(t_start / frame_sec))
    i1 = int(math.ceil(t_end / frame_sec))

    # 視窗不足一幀 → 取中點所在幀
    if i1 <= i0:
        mid = (t_start + t_end) / 2.0
        i0 = int(mid / frame_sec)
        i1 = i0 + 1

    # clip 到合法範圍
    i0 = max(0, i0)
    i1 = min(n, i1)

    if i0 >= i1:
        return NEG_INF

    window = envelope[i0:i1]
    rms_val = float(np.sqrt(np.mean(window * window)))
    eps = 1e-9
    return 20.0 * math.log10(rms_val + eps)


def assign_speakers_per_word(
    words: list,
    envelopes: dict,
    *,
    offsets: Optional[dict] = None,
    params: DiarizeParams,
) -> list:
    """
    對每個字：各軌取 rms_db_in_window → argmax → 套 margin/遲滯/最短段規則。
    回與 words 等長的 speaker 陣列（None＝靜音/無主）。純函式、不碰檔案。
    """
    if offsets is None:
        offsets = {}

    keys = list(envelopes.keys())
    n = len(words)
    if n == 0 or not keys:
        return []

    # ── 第一遍：逐字判（margin + hysteresis）──
    assignments: list = [None] * n
    prev_spk: Optional[str] = None

    for idx, word in enumerate(words):
        start = float(word["start"])
        end = float(word["end"])

        # 各軌 dB
        db: dict = {}
        for k, (env, fs) in envelopes.items():
            off = float(offsets.get(k, 0.0))
            db[k] = rms_db_in_window(env, fs, start, end, offset=off)

        # 靜音/無主判定
        champion_key = max(keys, key=lambda k: db[k])
        if db[champion_key] < params.silence_floor_db:
            assignments[idx] = None
            # prev_spk 不更新（保持前一個已知講者）
            continue

        # margin 門檻
        sorted_keys = sorted(keys, key=lambda k: db[k], reverse=True)
        champion = sorted_keys[0]
        runner_db = db[sorted_keys[1]] if len(sorted_keys) > 1 else NEG_INF
        margin_ok = (db[champion] - runner_db) >= params.margin_db

        if not margin_ok:
            # 不確定 → 沿用前一個已定講者；若無前值則暫記 champion
            if prev_spk is not None:
                assignments[idx] = prev_spk
            else:
                assignments[idx] = champion
                prev_spk = champion
            continue

        # 遲滯/黏著：若前講者與 champion 不同，需超過 hysteresis 才換
        if prev_spk is not None and prev_spk != champion:
            if db[champion] - db[prev_spk] < params.hysteresis_db:
                assignments[idx] = prev_spk
                continue

        assignments[idx] = champion
        prev_spk = champion

    # ── 第二遍：最短講者段後處理（去抖，吸收短附和）──
    assignments = _merge_short_turns(words, assignments, params)

    return assignments


def _merge_short_turns(
    words: list,
    assignments: list,
    params: DiarizeParams,
) -> list:
    """
    找出連續同講者的 run；若某 run 時長 < min_turn_sec 且字數 < min_turn_words，
    把它併入鄰段（直接併回左鄰，若無左鄰則接右鄰）。None 段不參與。

    反應詞閘門：若短 run 整段文字是反應詞（對/好/嗯/哈哈…），視為「短但確實是別人
    的附和」，保留成獨立段不併回——這類短插話正是我們想切出來的；非反應詞的短 run
    （中段某字被別軌能量瞬間壓過的碎字）才照舊併回。
    """
    reaction_set = set(params.reaction_words)
    result = list(assignments)
    changed = True
    max_iter = 20  # 防無限迴圈
    iteration = 0
    while changed and iteration < max_iter:
        changed = False
        iteration += 1
        # 找所有 run
        runs: list = []  # (start_idx, end_idx_exclusive, speaker)
        i = 0
        n = len(result)
        while i < n:
            spk = result[i]
            j = i
            while j < n and result[j] == spk:
                j += 1
            runs.append((i, j, spk))
            i = j

        new_result = list(result)
        for run_idx, (ri, rj, spk) in enumerate(runs):
            if spk is None:
                continue
            # 計算此 run 的時長與字數
            run_words = words[ri:rj]
            n_words = rj - ri
            if n_words == 0:
                continue
            dur = float(run_words[-1]["end"]) - float(run_words[0]["start"])

            # 觸發條件：時長 < min_turn_sec 且字數 < min_turn_words
            if dur < params.min_turn_sec and n_words < params.min_turn_words:
                # 反應詞閘門：整段是反應詞 → 保留成獨立段，不併回
                run_text = "".join(
                    (w.get("w") or w.get("text") or "") for w in run_words
                )
                if run_text in reaction_set:
                    continue
                # 找左右鄰的非 None speaker
                left_spk = None
                for ri2 in range(run_idx - 1, -1, -1):
                    if runs[ri2][2] is not None:
                        left_spk = runs[ri2][2]
                        break
                right_spk = None
                for ri2 in range(run_idx + 1, len(runs)):
                    if runs[ri2][2] is not None:
                        right_spk = runs[ri2][2]
                        break

                target = left_spk if left_spk is not None else right_spk
                if target is not None and target != spk:
                    for k in range(ri, rj):
                        new_result[k] = target
                    changed = True

        result = new_result

    return result


def cards_from_assignments(
    words: list,
    assignments: list,
    *,
    params: DiarizeParams,
) -> list:
    """
    依 §3 規則切卡：換人強制斷；同人內依長度/gap/問號/反應詞切。
    保證輸出無跨講者卡。text＝該卡 words 的 w 直接串接（無空格）。
    """
    reaction_set = set(params.reaction_words)

    def _is_dangling(text: str) -> bool:
        return any(text.endswith(d) for d in params.dangle_endings)

    cards: list = []
    n = len(words)
    if n == 0:
        return []

    # 先按講者切出連續同講者的候選段（None 字跳過）
    # 策略：線性掃描，碰到換人或 None 邊界就先封段，再對每段做同人內切
    segments: list = []  # (i0, i1, speaker)  — i1 exclusive
    i = 0
    while i < n:
        spk = assignments[i]
        if spk is None:
            i += 1
            continue
        j = i
        while j < n and assignments[j] == spk:
            j += 1
        segments.append((i, j, spk))
        i = j

    for seg_i0, seg_i1, spk in segments:
        seg_words = words[seg_i0:seg_i1]
        # 同人段內再切
        sub_cards = _split_segment(seg_words, seg_i0, spk, params, reaction_set, _is_dangling)
        cards.extend(sub_cards)

    return cards


def _split_segment(
    seg_words: list,
    global_offset: int,
    speaker: str,
    params: DiarizeParams,
    reaction_set: set,
    is_dangling,
) -> list:
    """同人段內切卡：優先切在 Breeze `seg` 句界。

    words.json 每字帶 `seg`（Breeze 語句分段編號），是唯一乾淨的句界訊號
    （轉錄輸出幾乎無標點）。策略：
      1. 依 seg 把同人段分成連續句群（相鄰同 seg 為一句）。
      2. 相鄰短句在 <=maxlen 內併成一卡（避免碎片化），與前句 gap>gapmax 則不併。
      3. 單一 seg 句本身超過 maxlen（少數長句）才退回長度貪婪切 `_greedy_length_split`。
      4. 反應詞句（對啊/好…）獨立成卡，不與鄰句合併。
    這樣切點永遠落在 Breeze 句界，不會把一個詞從中間劈開。
    """
    n = len(seg_words)
    if n == 0:
        return []

    # 1) 依 seg 切句群（半開區間，相對 seg_words）
    groups: list = []
    gs = 0
    for idx in range(1, n):
        if seg_words[idx].get("seg") != seg_words[idx - 1].get("seg"):
            groups.append((gs, idx))
            gs = idx
    groups.append((gs, n))

    cards: list = []
    cur_start = None  # 累積中卡片的起點（相對 seg_words）；None＝目前無累積

    def _text(lo: int, hi: int) -> str:
        return "".join(seg_words[k]["w"] for k in range(lo, hi))

    def _flush(end: int) -> None:
        nonlocal cur_start
        if cur_start is not None and end > cur_start:
            cards.append(
                _make_card(
                    seg_words, cur_start, end, speaker,
                    global_offset + cur_start, global_offset + end,
                )
            )
        cur_start = None

    for g0, g1 in groups:
        g_text = _text(g0, g1)

        # 超長句：先收掉手上的卡，再對此句內部跑長度貪婪切
        if len(g_text) > params.maxlen:
            _flush(g0)
            cards.extend(
                _greedy_length_split(
                    seg_words, g0, g1, global_offset, speaker,
                    params, reaction_set, is_dangling,
                )
            )
            continue

        # 反應詞句：獨立成卡
        if g_text in reaction_set:
            _flush(g0)
            cur_start = g0
            _flush(g1)
            continue

        # 一般句：嘗試併入目前卡
        if cur_start is None:
            cur_start = g0
            continue
        gap = float(seg_words[g0]["start"]) - float(seg_words[g0 - 1]["end"])
        if len(_text(cur_start, g1)) <= params.maxlen and gap <= params.gapmax:
            continue  # 併入（cur_start 不動，卡尾延伸到 g1）
        _flush(g0)
        cur_start = g0

    _flush(n)
    return cards


def _greedy_length_split(
    seg_words: list,
    lo: int,
    hi: int,
    global_offset: int,
    speaker: str,
    params: DiarizeParams,
    reaction_set: set,
    is_dangling,
) -> list:
    """單一超長 seg 句內的長度貪婪切（原 §3 規則：gap/問號/超長 dangle 放寬）。

    僅在單句字數 > maxlen 時被 `_split_segment` 呼叫。行為與舊版 `_split_segment`
    相同，只是作用範圍限縮在 seg_words[lo:hi]。
    """
    cards: list = []
    cur_start = lo
    cur_text = ""

    for idx in range(lo, hi):
        word = seg_words[idx]
        w = word["w"]

        # 計算與前一字的 gap
        if idx > cur_start:
            prev_end = float(seg_words[idx - 1]["end"])
            gap = float(word["start"]) - prev_end
        else:
            gap = 0.0

        # 反應詞獨立成卡（整個 cur_text 是反應詞）
        if cur_text and cur_text in reaction_set:
            i0 = global_offset + cur_start
            i1 = global_offset + idx
            cards.append(_make_card(seg_words, cur_start, idx, speaker, i0, i1))
            cur_start = idx
            cur_text = w
            continue

        candidate_text = cur_text + w

        # gap 觸發斷卡（在累積之前）
        if idx > cur_start and gap > params.gapmax:
            i0 = global_offset + cur_start
            i1 = global_offset + idx
            cards.append(_make_card(seg_words, cur_start, idx, speaker, i0, i1))
            cur_start = idx
            cur_text = w
            continue

        # 問號結尾觸發（已滿 5 字且尾字 ∈ qend_chars）
        if len(cur_text) >= 5 and cur_text[-1:] in params.qend_chars:
            i0 = global_offset + cur_start
            i1 = global_offset + idx
            cards.append(_make_card(seg_words, cur_start, idx, speaker, i0, i1))
            cur_start = idx
            cur_text = w
            continue

        # 超長觸發（> maxlen）：dangle 放寬
        over_maxlen = len(candidate_text) > params.maxlen
        if over_maxlen:
            allow_dangle = len(candidate_text) <= params.hardlen and is_dangling(cur_text)
            if not allow_dangle:
                # 在加入 w 之前斷（斷在前一字後面）
                if idx > cur_start:
                    i0 = global_offset + cur_start
                    i1 = global_offset + idx
                    cards.append(_make_card(seg_words, cur_start, idx, speaker, i0, i1))
                    cur_start = idx
                    cur_text = w
                    continue
                # 若當前只有 0 個字（cur_start==idx），還是要加進去，不然會死循環
                cur_text = candidate_text
                continue

        cur_text = candidate_text

    # 封尾段
    if cur_start < hi:
        i0 = global_offset + cur_start
        i1 = global_offset + hi
        cards.append(_make_card(seg_words, cur_start, hi, speaker, i0, i1))

    return cards


def _make_card(
    seg_words: list,
    local_start: int,
    local_end: int,   # exclusive
    speaker: str,
    global_i0: int,
    global_i1: int,
) -> Card:
    """從 seg_words[local_start:local_end] 組建 Card dict。"""
    span_words = seg_words[local_start:local_end]
    text = "".join(w["w"] for w in span_words)
    start = float(span_words[0]["start"])
    end = float(span_words[-1]["end"])
    return {
        "start": start,
        "end": end,
        "text": text,
        "speaker": speaker,
        "word_span": (global_i0, global_i1),
    }


def write_outputs(
    ep,
    cards: list,
    *,
    backup: bool = True,
) -> tuple:
    """
    備份既有 → 寫 _final_v2.srt（srt_io）+ speakers.json（cameras_io）。
    回 (srt_path, speakers_json_path)。
    """
    from podcast_toolkit import cameras_io, srt_io

    srt_path = ep.output_v2_srt()
    spk_path = ep.output_v2_speakers_json()

    if backup:
        _backup_outputs(ep)

    # 確保輸出目錄存在
    srt_path.parent.mkdir(parents=True, exist_ok=True)

    # 產 SRT 用的 card dict（srt_io.serialize 吃 idx/start/end/text）
    srt_cards = [
        {"idx": i + 1, "start": c["start"], "end": c["end"], "text": c["text"]}
        for i, c in enumerate(cards)
    ]
    srt_text = srt_io.serialize(srt_cards)
    srt_path.write_text(srt_text, encoding="utf-8")

    # 寫 speakers.json：{1-based idx: speaker_key}
    mapping = {i + 1: c["speaker"] for i, c in enumerate(cards)}
    cameras_io.save(spk_path, mapping)

    return srt_path, spk_path


def _backup_outputs(ep) -> list:
    """複製 _final_v2.srt + .speakers.json → {stem}.{stamp}.bak{suffix}。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backed: list = []
    for src in (ep.output_v2_srt(), ep.output_v2_speakers_json()):
        if not Path(src).exists():
            continue
        dst = src.with_name(f"{src.stem}.{stamp}.bak{src.suffix}")
        dst.write_bytes(src.read_bytes())
        backed.append(str(dst))
    return backed


# ──────────────────────────────────────────────
# §1.3 orchestrator
# ──────────────────────────────────────────────

def run(
    ep,
    *,
    force: bool = False,
    progress: Optional[Callable] = None,
) -> int:
    """
    1. mics = ep.mic_paths()；空 → return 4。
    2. words_path = find_words_json(ep)；無 → return 3。
    3. 輸出已存在且 not force → return 1。
    4. 讀各軌 envelope、assign、cards、write。
    5. 進度 phase ∈ {"read-mics","assign","segment","write","done"}。
    回 exit code（0 成功）。
    """
    from podcast_toolkit.vad_gate import _read_pcm_mono

    def _progress(phase: str, msg: str = "") -> None:
        if progress is not None:
            progress(phase, msg)

    # 1. mics
    mics = ep.mic_paths()
    if not mics:
        print("[mic_diarize] 缺 mic_paths 設定，無法執行分軌。")
        return 4

    # 2. words.json
    words_path = find_words_json(ep)
    if words_path is None:
        print("[mic_diarize] 找不到 *_字幕_words.json，請先跑混音轉錄。")
        return 3

    # 3. 覆寫守門
    srt_out = ep.output_v2_srt()
    if Path(srt_out).exists() and not force:
        print(f"[mic_diarize] 輸出已存在（{srt_out}），用 force=True 覆寫。")
        return 1

    # 4. 讀 params
    cfg = getattr(ep, "cfg", {}) or {}
    md_cfg = cfg.get("mic_diarize", {}) or {}
    rs_cfg = cfg.get("resegment", {}) or {}

    params = DiarizeParams(
        frame_ms=int(md_cfg.get("frame_ms", 20)),
        margin_db=float(md_cfg.get("margin_db", 3.0)),
        silence_floor_db=float(md_cfg.get("silence_floor_db", -45.0)),
        min_turn_sec=float(md_cfg.get("min_turn_sec", 0.6)),
        min_turn_words=int(md_cfg.get("min_turn_words", 2)),
        hysteresis_db=float(md_cfg.get("hysteresis_db", 1.5)),
        maxlen=int(rs_cfg.get("maxlen", 17)),
        hardlen=int(rs_cfg.get("hardlen", 23)),
        gapmax=float(rs_cfg.get("gapmax", 0.6)),
        qend_chars=str(rs_cfg.get("qend_chars", "嗎呢")),
        reaction_words=tuple(rs_cfg.get("reaction_words", DiarizeParams().reaction_words)),
        dangle_endings=tuple(rs_cfg.get("dangle_endings", DiarizeParams().dangle_endings)),
    )

    offsets_raw = md_cfg.get("offsets", {}) or {}
    offsets = {k: float(v) for k, v in offsets_raw.items()}

    # 讀各軌 envelope（逐軌讀→算→釋放 raw，§8 記憶體建議）
    _progress("read-mics", f"讀取 {len(mics)} 軌分軌音檔")
    envelopes: dict = {}
    for key, mic_path in mics.items():
        mic_path = Path(mic_path)
        if not mic_path.exists():
            print(f"[mic_diarize] 找不到分軌檔：{mic_path}，跳過。")
            continue
        samples = _read_pcm_mono(mic_path, sample_rate=16000)
        env, fs = compute_rms_envelope(samples, 16000, frame_ms=params.frame_ms)
        envelopes[key] = (env, fs)
        del samples  # 釋放 raw

    if not envelopes:
        print("[mic_diarize] 所有 mic 檔案讀取失敗。")
        return 4

    # 讀 words
    words = load_words(words_path)
    n_words = len(words)
    print(f"[mic_diarize] 載入 {n_words} 個字（{words_path.name}）")

    # assign
    _progress("assign", f"逐字判講者（{n_words} 字，{len(envelopes)} 軌）")
    assignments = assign_speakers_per_word(words, envelopes, offsets=offsets, params=params)

    # 統計丟棄字數（§8 風險⑦）
    n_dropped = sum(1 for a in assignments if a is None)
    if n_dropped:
        print(f"[mic_diarize] 警告：{n_dropped}/{n_words} 個字因靜音/全軌低能量被丟棄（可能缺字幕）。")

    # cards
    _progress("segment", "依規則切卡")
    cards = cards_from_assignments(words, assignments, params=params)
    print(f"[mic_diarize] 切出 {len(cards)} 張字幕卡")

    # write
    _progress("write", "寫出 SRT + speakers.json")
    srt_p, spk_p = write_outputs(ep, cards, backup=True)
    print(f"[mic_diarize] 已寫出：{srt_p.name}，{spk_p.name}")

    _progress("done", "完成")
    return 0
