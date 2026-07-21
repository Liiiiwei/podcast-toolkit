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

# ── 主詞掛尾後處理常數 ──────────────────────────────
# 依長度由長到短排列，比對時長詞優先
SUBJECT_TOKENS: list = [
    "我們", "你們", "妳們", "他們", "她們", "它們", "咱們",
    "這個", "那個", "這些", "那些",
    "大家",
    "我", "你", "妳", "他", "她", "它", "咱",
]

# T 前一字元屬此集合 → T 是受詞，不搬（改動 1：擴充及物動詞末字 + 授受/使役動詞）
OBJECT_VETO: set = set(
    "跟對幫替向給被把和與同陪找看問想教叫讓帶送約請救騙害等靠怪勸罵誇念管顧謝信疼愛恨怕欠娶嫁"
) | {
    "識", "資", "醒",   # 認識/投資/提醒 的末字也視為受詞語境
} | set(
    # 及物動詞末字：訴 知 託 護 煩 服 見 碰 覆 絡 福 喜 嚀 賞 勸 誇 罵 顧 疼 佩 敬 求 謝 娶 嫁 救 騙 害 顧
    "訴知託護煩服見碰覆絡福喜嚀賞誇顧疼佩敬求"
    # 授受/使役動詞（直接管後接代名詞當受詞）
    # 讓/叫/使/令/請/派/逼/勸/求/陪/幫/替/教 已在主集合或下行新增
    "令派逼陪"
)

# 介副詞/介詞前字集合 → T 前一字屬此集合，T 是介補語，直接否決
COVERB_PREP: set = set("跟對幫替向被把為於由從在給和與同陪關比朝往據靠")

# 動補/趨向/體標記：遇到這些字要再往前看一字確認真動詞
_ASPECT: set = set("到著過了住完起見給掉走上下出回開")

# 謂語起頭一字（B 卡首字命中 → 佐證 T 是 B 的主詞）
PREDICATE_STARTERS_1: set = set(
    "就才也都還又再會要想說講做去來選決沒不是有把被讓反甚只知必應能該覺一其已可而便"
)
# 謂語起頭二字
PREDICATE_STARTERS_2: set = {
    "覺得", "可以", "已經", "其實", "應該", "必須", "反而", "甚至",
    "一定", "沒有", "不是", "還是", "知道", "選擇", "決定", "可能",
    "就是", "才是", "開始",
}
# B 卡首字為補語/量詞開頭 → 排除（不是謂語起頭）
_COMP_STARTERS: set = set("的得地之們個")

# ── jieba 本地斷詞（可選相依，零雲端）──────────────
# 用途：超長句長度硬切時，把切點退回 jieba 詞界，避免把「標準」「董事長」
# 這類詞從中間劈開。缺 jieba 時整個功能靜默退化為原字元切（不報錯）。
try:
    import jieba as _jieba
    _jieba.setLogLevel(60)  # 靜音啟動 log（dict 載入訊息）
except Exception:  # pragma: no cover - 環境無 jieba 時
    _jieba = None


def _jieba_char_boundaries(text: str):
    """回傳 text 的 jieba 詞界「字元位移」集合（含 0 與 len(text)）。

    切點只要落在此集合內，就不會劈開任何 jieba 認得的詞。jieba 不可用或
    text 為空時回 None，呼叫端據此退回原字元切。
    """
    if _jieba is None or not text:
        return None
    bounds = {0}
    pos = 0
    for tok in _jieba.cut(text, HMM=True):
        pos += len(tok)
        bounds.add(pos)
    return bounds


def _glossary_terms(terms):
    """從各種 glossary 形態抽出要保護的字串詞。

    支援：以逗號/空白分隔的字串；str 清單；dict 清單（取 `canonical`＋
    `sounds_like`，對齊 episode.yaml glossary 結構）。回去重後的字串清單。
    """
    if not terms:
        return []
    if isinstance(terms, str):
        terms = [t for t in terms.replace("，", " ").replace(",", " ").split() if t]
    out: list = []
    for item in terms:
        if isinstance(item, dict):
            cano = str(item.get("canonical", "")).strip()
            if cano:
                out.append(cano)
            for s in item.get("sounds_like", []) or []:
                s = str(s).strip()
                if s:
                    out.append(s)
        else:
            s = str(item).strip()
            if s:
                out.append(s)
    # 去重保序
    seen: set = set()
    uniq = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def load_jieba_userdict(terms) -> int:
    """把片語/人名逐一加入 jieba 詞庫，保護它們不被切散（如來賓藝名、公司名）。

    terms 接受 episode.yaml glossary 的各種形態（見 `_glossary_terms`）；
    空值安全略過。回實際加入的詞數。jieba 不可用時回 0。
    """
    if _jieba is None:
        return 0
    n = 0
    for t in _glossary_terms(terms):
        _jieba.add_word(t)
        n += 1
    return n


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
    # 主詞掛尾後處理開關（True = 開啟，False = 跳過）
    subject_shift: bool = True


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

    # 主詞掛尾後處理（高精度，寧漏勿錯）
    if params.subject_shift:
        cards = _shift_trailing_subjects(cards, words, params)

    return cards


def _shift_trailing_subjects(cards: list, words: list, params: DiarizeParams) -> list:
    """主詞掛尾後處理：把「卡尾屬於下一卡的主詞」從 A 卡搬到 B 卡頭。

    高精度優先：需同時滿足「前提」＋「搬移條件」才搬；任一不符就跳過。
    單趟掃描，不對同批卡二次搬。純函式，不修改輸入 list 或 dict。
    """
    if len(cards) < 2:
        return list(cards)

    result: list = []
    i = 0
    # 預先建立 words 索引→word 的快取（words 本身已是 list，直接用下標）
    while i < len(cards):
        if i + 1 >= len(cards):
            result.append(cards[i])
            i += 1
            continue

        A = cards[i]
        B = cards[i + 1]

        # ── 前提：同講者、word_span 連續、gap 合法 ──
        a0, a1 = A["word_span"]
        b0, b1 = B["word_span"]

        if A["speaker"] != B["speaker"] or a1 != b0:
            result.append(A)
            i += 1
            continue

        # gap = words[b0].start - words[a1-1].end
        if a1 == 0 or b0 >= len(words) or (a1 - 1) >= len(words):
            result.append(A)
            i += 1
            continue

        gap = float(words[b0]["start"]) - float(words[a1 - 1]["end"])
        if gap > params.gapmax:
            result.append(A)
            i += 1
            continue

        # ── 搬移條件 1：A 尾以 SUBJECT_TOKENS 某詞結尾 ──
        a_text = A["text"]
        matched_token = None
        for tok in SUBJECT_TOKENS:  # 已按長度由長到短排
            if a_text.endswith(tok):
                matched_token = tok
                break

        if matched_token is None:
            result.append(A)
            i += 1
            continue

        T = matched_token
        k_len = len(T)

        # ── 搬移條件 2：從 A 尾往前累積 word 剛好拼出 T（word 邊界對齊）──
        # 逐字從 a1-1 往回累積，看是否能剛好覆蓋 T
        accumulated = ""
        k = 0  # 要搬的 word 數
        j = a1 - 1
        while j >= a0:
            accumulated = words[j]["w"] + accumulated
            k += 1
            if accumulated == T:
                break
            if len(accumulated) >= k_len:
                # 已超過 T 長度或組不出來
                k = 0
                break
            j -= 1
        else:
            # 掃完 A 還組不出 T
            k = 0

        if k == 0:
            result.append(A)
            i += 1
            continue

        # ── 搬移條件 3：受詞否決（改動 2：升級回看邏輯）──
        # prefix_text = A 的 text 去掉尾端主詞 T 後的字串
        prefix_text = a_text[: len(a_text) - k_len]
        _veto = False
        if prefix_text:
            c1 = prefix_text[-1]
            if c1 in COVERB_PREP:
                # 介副詞/介詞前字 → T 是介補語，直接否決
                _veto = True
            elif c1 in _ASPECT and len(prefix_text) >= 2:
                # 動補標記：回看第二字確認真動詞或介詞
                c2 = prefix_text[-2]
                if c2 in OBJECT_VETO or c2 in COVERB_PREP:
                    _veto = True
            elif c1 in OBJECT_VETO:
                # 直接否決（單字回看）
                _veto = True
        if _veto:
            result.append(A)
            i += 1
            continue

        # ── 搬移條件 4：B 卡首字/前綴 ∈ PREDICATE_STARTERS ──
        b_text = B["text"]
        if not b_text:
            result.append(A)
            i += 1
            continue

        # 排除 B 以補語/量詞開頭
        if b_text[0] in _COMP_STARTERS:
            result.append(A)
            i += 1
            continue

        b_pred_ok = (
            b_text[:2] in PREDICATE_STARTERS_2
            or b_text[0] in PREDICATE_STARTERS_1
        )
        if not b_pred_ok:
            result.append(A)
            i += 1
            continue

        # ── 搬移動作 ──
        spk = A["speaker"]
        if k < (a1 - a0):
            # A 縮：保留 a0 ~ a1-k，T 移入 B 頭
            new_a1 = a1 - k
            new_b0 = a1 - k  # == new_a1
            new_A = _make_card(words, a0, new_a1, spk, a0, new_a1)
            new_B = _make_card(words, new_b0, b1, spk, new_b0, b1)
            result.append(new_A)
            # 直接把修改後的 B 覆蓋回 cards[i+1] 位置（以 new_B 取代 B）
            cards = list(cards)  # 複製避免改原輸入
            cards[i + 1] = new_B
        else:
            # A 整卡就是主詞 → 合併 A+B 成一張大 B
            new_B = _make_card(words, a0, b1, spk, a0, b1)
            cards = list(cards)
            cards[i + 1] = new_B
            # A 不輸出（合併掉）
            i += 1
            continue

        i += 1

    # 補上最後一張（若沒被合併）
    # 注意：上面 while 迴圈已把除合併外的所有卡 append 進 result，
    # 最後一張在 i == len(cards)-1 時由首個分支 append。
    return result


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

    差異：長度硬切（over_maxlen）的切點會退回最近的 jieba 詞界，避免把
    「標準」「董事長」這類詞從中間劈開；jieba 不可用、或整段無合法內部詞界
    （單一 jieba 詞就超過 maxlen）時，退回原字元切以保證進度、不死循環。
    """
    cards: list = []
    cur_start = lo
    cur_text = ""

    # 預算 jieba 詞界：full_text 為 seg_words[lo:hi] 串接文字，
    # boundaries 是相對 lo 的字元位移集合；char_at[k] = token k 起點的字元位移。
    full_text = "".join(seg_words[k]["w"] for k in range(lo, hi))
    jieba_bounds = _jieba_char_boundaries(full_text)
    char_at: dict = {}
    _off = 0
    for k in range(lo, hi + 1):
        char_at[k] = _off
        if k < hi:
            _off += len(seg_words[k]["w"])

    def _snap_cut(start_idx: int, cut_idx: int) -> int:
        """把想切在 token cut_idx 前的切點，退回 (start_idx, cut_idx] 內最近的
        jieba 詞界 token。找不到就回 cut_idx（原字元切）。回值恆 > start_idx，
        保證每次切都推進，不會死循環。"""
        if jieba_bounds is None:
            return cut_idx
        for j in range(cut_idx, start_idx, -1):
            if char_at[j] in jieba_bounds:
                return j
        return cut_idx

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
                # 在加入 w 之前斷（斷在前一字後面）；切點退回 jieba 詞界
                if idx > cur_start:
                    cut_idx = _snap_cut(cur_start, idx)
                    i0 = global_offset + cur_start
                    i1 = global_offset + cut_idx
                    cards.append(_make_card(seg_words, cur_start, cut_idx, speaker, i0, i1))
                    cur_start = cut_idx
                    # 退回詞界後，cut_idx..idx（含 w）尚未成卡，續累積
                    cur_text = "".join(seg_words[k]["w"] for k in range(cut_idx, idx + 1))
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

    # 載入本集 glossary（來賓藝名、公司名…）進 jieba 詞庫，保護其不被切界劈開
    gloss = cfg.get("glossary")
    n_terms = load_jieba_userdict(gloss)
    if n_terms:
        print(f"[mic_diarize] 已載入 {n_terms} 個 glossary 詞進 jieba 保護詞庫")

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
