"""從逐字 SRT(Gemini _final.srt,每字一張卡)重斷成乾淨句卡。

跟 toolkit 既有 `resegment` 的差異(這是另一種風格,不改既有行為):
- **斷點優先序:句末(。！？/嗎呢)> 子句(，、)> 停頓(gap)> 不得已才到 maxlen** —— 既有 resegment
  只會貪婪併到 maxlen 然後硬切,會切斷詞(例「賽/道」);這裡優先在乾淨邊界斷。
- **過短卡併進鄰卡**(消掉「一兩個字孤卡」),但不跨大停頓硬併(避免把卡拉過長靜音)。
- **標點正規化**:逗號/頓號 → 半形空格(保留停頓視覺)、句末標點 / 引號括號去除。
- **時間精準**:卡首 = 第一個非標點字的起、卡尾 = 最後一個非標點字的結束。
  (逐字檔裡標點常吸了後面整段靜音,例 `。[1.92→15.94]`,所以要用「最後實字」的結束。)

純函式 `segment_chars` 不碰檔案;`run` 讀 _final.srt → 寫 _v2.srt(呼叫端負責備份)。
"""
from __future__ import annotations

import re

# 句末符號:斷在它後面(一句結束)
_SENT_ENDERS = set("。！？!?")
# 語氣問句尾字:卡夠長才當句末斷(避免「嗎」單獨成短卡)
_QEND = set("嗎呢")
# 停頓類標點:正規化成空格(保留停頓視覺)。含子句逗號 + 句末符號 —— 句末通常落在卡尾被收掉,
# 但萬一短卡相併讓它落到卡中間,換成空格才不會「整理嗎其實」黏在一起。
_TO_SPACE = set("，,、。！？!?；;：:")
# 子句分隔(次要斷點 + 併卡判斷用):逗號 / 頓號
_CLAUSE = set("，,、")
# 直接刪除的標點(引號括號 / 刪節號 / 裝飾符)
_REMOVE = set("…⋯「」『』“”‘’\"'（）()【】《》〈〉<>·•～~")


def _is_punct(ch: str) -> bool:
    return ch in _TO_SPACE or ch in _REMOVE or ch.isspace()


def normalize_text(chars: list[str]) -> str:
    """把一串字元正規化成字幕文字:逗號→空格、句末/引號標點去除、收斂多空格。"""
    out: list[str] = []
    for ch in chars:
        if ch in _TO_SPACE or ch.isspace():
            out.append(" ")
        elif ch in _REMOVE:
            continue
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _disp_len(buf: list) -> int:
    """顯示字數(不含標點 / 空白)。"""
    return sum(1 for _, _, ch in buf if not _is_punct(ch))


def _max_gap_idx(buf: list, min_gap: float) -> int | None:
    """buf 裡內部最大停頓的切點(後一個位置),停頓需 > min_gap。"""
    best_i, best_gap = None, min_gap
    for j in range(len(buf) - 1):
        gap = buf[j + 1][0] - buf[j][1]
        if gap > best_gap:
            best_gap, best_i = gap, j + 1
    return best_i


def _clause_pieces(buf: list) -> list[list]:
    """在子句分隔符(逗號 / 頓號)後切成片段,每段是一個子句。"""
    pieces, cur = [], []
    for tok in buf:
        cur.append(tok)
        if tok[2] in _CLAUSE:
            pieces.append(cur)
            cur = []
    if cur:
        pieces.append(cur)
    return pieces


def _split_long_piece(buf: list, maxlen: int, min_gap: float) -> list[list]:
    """單一子句(無逗號可切)還是超過 maxlen → 切最大停頓 > 不得已硬切。"""
    if _disp_len(buf) <= maxlen:
        return [buf]
    k = _max_gap_idx(buf, min_gap)
    if not (k and 2 <= k <= len(buf) - 1):
        k, n = None, 0                          # 硬切:第一個讓顯示字數達 maxlen 的位置
        for j, (_, _, ch) in enumerate(buf):
            if not _is_punct(ch):
                n += 1
            if n >= maxlen:
                k = j + 1
                break
    if not k or k >= len(buf):
        return [buf]
    return _split_long_piece(buf[:k], maxlen, min_gap) + _split_long_piece(buf[k:], maxlen, min_gap)


def _split_oversized(buf: list, maxlen: int, min_gap: float) -> list[list]:
    """超過 maxlen 的段:切成子句片段 → **前向貪婪**打包到 maxlen(卡儘量長、邊界乾淨)。"""
    if _disp_len(buf) <= maxlen:
        return [buf]
    flat: list[list] = []
    for p in _clause_pieces(buf):
        flat.extend(_split_long_piece(p, maxlen, min_gap))   # 太長的子句先拆
    out: list[list] = []
    acc: list = []
    for p in flat:
        if acc and _disp_len(acc) + _disp_len(p) > maxlen:
            out.append(acc)
            acc = p
        else:
            acc = acc + p
    if acc:
        out.append(acc)
    return out


def _merge_short(cards: list[list], min_chars: int, gapmax: float) -> list[list]:
    """把顯示字數 < min_chars 的卡併進「停頓較小」的鄰卡;兩側都隔大停頓就保留(真‧獨立短語)。"""
    if not cards:
        return cards
    out = [cards[0]]
    for cur in cards[1:]:
        prev = out[-1]
        if _disp_len(cur) < min_chars or _disp_len(prev) < min_chars:
            gap = cur[0][0] - prev[-1][1]
            if gap <= gapmax:
                out[-1] = prev + cur          # 併進前卡(時間連續才併)
                continue
        out.append(cur)
    return out


def segment_chars(
    char_cards: list[dict],
    *,
    maxlen: int = 20,
    min_chars: int = 3,
    gapmax: float = 0.6,
) -> list[dict]:
    """逐字卡 → 乾淨句卡。char_cards:每張 {start,end,text}(text 通常一字)。

    回 [{idx,start,end,text}],idx 從 1 重編,時間精準對齊原逐字時間。
    """
    chars = []
    for c in char_cards:
        for ch in (c.get("text") or ""):       # 防禦:一張卡若多字,逐字攤開(時間共用該卡)
            chars.append((c["start"], c["end"], ch))
    if not chars:
        return []

    # 主迴圈只在「自然邊界」斷:句末 / 語氣問句尾 / 大停頓。
    # 長度控制留給 _split_oversized(切子句 > 停頓 > 不得已硬切),
    # 不在這裡硬切 maxlen —— 否則會把「我愛上班 / 賽道」這種詞切兩半。
    raw: list[list] = []
    buf: list = []
    for i, (st, en, ch) in enumerate(chars):
        buf.append((st, en, ch))
        nxt_gap = (chars[i + 1][0] - en) if i + 1 < len(chars) else 1e9
        if ch in _SENT_ENDERS or (ch in _QEND and _disp_len(buf) >= 4) or nxt_gap > gapmax:
            raw.append(buf)
            buf = []
    if buf:
        raw.append(buf)

    # 過長段再切(子句 > 停頓 > 硬切)
    split: list[list] = []
    for b in raw:
        split.extend(_split_oversized(b, maxlen, gapmax))

    merged = _merge_short(split, min_chars, gapmax)

    out: list[dict] = []
    for b in merged:
        real = [(s, e, ch) for s, e, ch in b if not _is_punct(ch)]
        text = normalize_text([ch for _, _, ch in b])
        if not real or not text:
            continue
        out.append({"idx": len(out) + 1, "start": real[0][0], "end": real[-1][1], "text": text})
    return out


def run(episode_dir, *, maxlen: int = 20, min_chars: int = 3, gapmax: float = 0.6) -> int:
    """讀 _final.srt(逐字)→ 重斷 → 覆寫 _v2.srt。回 exit code。呼叫端負責備份。"""
    import sys
    from pathlib import Path

    from podcast_toolkit import srt_io
    from podcast_toolkit.episode import Episode

    ep = Episode(Path(episode_dir))
    src = ep.output_srt()                       # 03_成品/{name}_final.srt(逐字 Gemini)
    if not src.exists():
        print(f"✗ 找不到逐字字幕:{src}", file=sys.stderr)
        return 3
    char_cards = srt_io.parse(src.read_text(encoding="utf-8"))
    cards = segment_chars(char_cards, maxlen=maxlen, min_chars=min_chars, gapmax=gapmax)
    out = ep.output_v2_srt()
    out.write_text(srt_io.serialize(cards), encoding="utf-8")
    print(f"重斷句:{len(char_cards)} 逐字卡 → {len(cards)} 句卡(maxlen={maxlen})")
    return 0
