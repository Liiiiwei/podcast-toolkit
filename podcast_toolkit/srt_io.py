"""SRT 解析與序列化。共用給 web/episode_io.py。"""
from __future__ import annotations
import re
from collections.abc import Iterable


_UNIT_RE = re.compile(r"(\d+)\s*(ms|h|m|s)", re.IGNORECASE)
_UNIT_MULT = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


# 中文 podcast 對話約 3-5 字/秒；用 0.3s/字當「合理語速」上界。
# 切卡時若原卡 dur 比 sum(chars)*RATE 還大（trailing silence），
# sub-cards 從 t0 緊湊排，尾段不指派字幕；避免 sub-card 1 被推進靜音裡。
SPLIT_SEC_PER_CHAR = 0.3


def allocate_split_times(
    t0: float, t1: float, parts: list[str]
) -> list[tuple[float, float]]:
    """把 [t0, t1] 依 parts 字數切成 N 段時間。

    若原卡夠長能容納「字數 × 合理語速」→ 從 t0 緊湊排，剩餘 trailing silence 不分配；
    若原卡比語速 budget 還短 → 退回比例分配，貼滿整段。
    """
    lengths = [max(len(p), 1) for p in parts]
    total = sum(lengths)
    dur = t1 - t0
    budget = total * SPLIT_SEC_PER_CHAR
    rate = SPLIT_SEC_PER_CHAR if budget <= dur else dur / total
    out: list[tuple[float, float]] = []
    cum = 0.0
    for ln in lengths:
        start = t0 + cum
        cum += ln * rate
        end = min(t0 + cum, t1)
        out.append((start, end))
    return out


def _ts2s(ts: str) -> float:
    # Gemini 不總是遵守 prompt 的 hh:mm:ss,ms 格式，實測會出現：
    #   - mm:ss,ms （省略小時段）
    #   - hh:mm:ss.ms （用 . 取代 , 分隔毫秒）
    #   - hh:mm:ss（完全省略毫秒）
    #   - 26m3s766ms（口語化單位寫法，完全沒冒號）
    # 寬容處理；無法分段才拋錯。
    cleaned = ts.strip()
    if ":" in cleaned:
        c = cleaned.replace(".", ",", 1)
        if "," in c:
            clock, ms_str = c.rsplit(",", 1)
        else:
            clock, ms_str = c, "0"
        bits = clock.split(":")
        if len(bits) == 2:
            h, m, s = "0", bits[0], bits[1]
        elif len(bits) == 3:
            h, m, s = bits
        else:
            raise ValueError(f"無法解析 srt 時間碼：{ts!r}")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms_str) / 1000
    matches = _UNIT_RE.findall(cleaned)
    if matches:
        return sum(int(n) * _UNIT_MULT[u.lower()] for n, u in matches)
    raise ValueError(f"無法解析 srt 時間碼：{ts!r}")


def seconds_to_srt_ts(t: float) -> str:
    """秒 → SRT timestamp（hh:mm:ss,SSS）。全套件共用的 cue 時間序列化。"""
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


_ARROW_RE = re.compile(r"^\s*\S+\s*-->\s*\S+\s*$")


def parse(text: str) -> list[dict]:
    """解析 srt 字串 → list of {idx, start, end, text}。idx 為 srt 原本的 1-based 序號。

    Gemini 偶爾會省略 cue 之間的空行（實測 mic_b 87 cues 0 空行）。
    這時 `split("\\n\\n")` 會把整個檔案吃成 1 個 block。
    所以無論空行有沒有，都用「掃描行」方式：尋找「純數字 idx → time arrow」這個 pattern
    當成新 cue 的起點，中間其他行歸給上一個 cue 的 text。
    """
    cleaned = text.replace("\r\n", "\n").strip()
    lines = cleaned.split("\n")
    cards: list[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        # 跳過空白
        if not lines[i].strip():
            i += 1
            continue
        # 找 idx + time arrow 模式
        if i + 1 < n and lines[i].strip().isdigit() and _ARROW_RE.match(lines[i + 1]):
            try:
                idx = int(lines[i].strip())
            except ValueError:
                i += 1
                continue
            start_str, _, end_str = lines[i + 1].partition(" --> ")
            try:
                start = _ts2s(start_str.strip())
                end = _ts2s(end_str.strip())
            except ValueError:
                i += 2
                continue
            text_lines: list[str] = []
            j = i + 2
            while j < n:
                # 下一個 cue 起點：純數字 + 緊接 time arrow → 停
                if (
                    lines[j].strip().isdigit()
                    and j + 1 < n
                    and _ARROW_RE.match(lines[j + 1])
                ):
                    break
                # 空行：當段落結束（若已是空行 + 下一行又空 → 仍可繼續，但實務上空行通常就是 cue 邊界）
                if not lines[j].strip() and text_lines:
                    j += 1
                    break
                text_lines.append(lines[j])
                j += 1
            cards.append(
                {
                    "idx": idx,
                    "start": start,
                    "end": end,
                    "text": "\n".join(text_lines).rstrip(),
                }
            )
            i = j
        else:
            i += 1
    return cards


def serialize(
    cards: Iterable[dict],
    overrides: dict[int, str] | None = None,
    splits: dict[int, list[str]] | None = None,
    time_overrides: dict[tuple[int, int], tuple[float, float]] | None = None,
) -> str:
    """把 cards 寫回 srt 字串。

    overrides[idx]：覆寫對應 card 的文字（在 split 之前先 apply）
    splits[idx]：把該卡切成 N 段文字，時間依文字長度比例分配；
                 切完所有 idx 一律重編序號。
    time_overrides[(idx, part)]：手動覆寫該卡 / 該段的 (start, end) 秒（最後一道覆寫）。
    """
    text, _ = serialize_with_map(
        cards, overrides=overrides, splits=splits, time_overrides=time_overrides
    )
    return text


def serialize_with_map(
    cards: Iterable[dict],
    overrides: dict[int, str] | None = None,
    splits: dict[int, list[str]] | None = None,
    time_overrides: dict[tuple[int, int], tuple[float, float]] | None = None,
) -> tuple[str, list[tuple[int, int]]]:
    """同 serialize，但額外回傳 idx_map：
    new_idx (1-based) → (original_idx, part_idx)
    part_idx：未切的卡固定 0；切的卡 0..N-1。
    給 caller 翻譯 cameras_mapping / deletions / textOverrides 用。

    time_overrides[(oid, part)]：手動拖拉改的時間，疊在最外層覆寫衍生值——
    未切卡覆寫 SRT 原始時間；切句卡覆寫 allocate_split_times 算出的該段時間
    （沒被覆寫的段仍走字數分配）。idx_map 不受影響（時間覆寫不改編號）。
    """
    overrides = overrides or {}
    splits = splits or {}
    time_overrides = time_overrides or {}
    out: list[str] = []
    idx_map: list[tuple[int, int]] = []
    new_idx = 1
    for c in cards:
        oid = c["idx"]
        base_text = overrides.get(oid, c["text"])
        parts = splits.get(oid)
        if parts and len(parts) > 1:
            times = allocate_split_times(c["start"], c["end"], parts)
            for i, (part, (p_start, p_end)) in enumerate(zip(parts, times)):
                st, en = time_overrides.get((oid, i), (p_start, p_end))
                out.append(f"{new_idx}\n{seconds_to_srt_ts(st)} --> {seconds_to_srt_ts(en)}\n{part}\n")
                idx_map.append((oid, i))
                new_idx += 1
        else:
            st, en = time_overrides.get((oid, 0), (c["start"], c["end"]))
            out.append(f"{new_idx}\n{seconds_to_srt_ts(st)} --> {seconds_to_srt_ts(en)}\n{base_text}\n")
            idx_map.append((oid, 0))
            new_idx += 1
    return "\n".join(out), idx_map
