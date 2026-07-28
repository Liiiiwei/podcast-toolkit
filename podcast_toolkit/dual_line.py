"""雙行字幕排版：兩位講者同時說話時，把時間重疊的字幕卡分成上下兩排。

前端預覽（web/static/app.js 的 renderCaption）早就會把同一時刻的多張卡疊成
上下兩行；這支模組把同一套規則搬到出片端，讓燒進成品的 ASS 也有雙行。

規則（與前端一致）：
- 只有「時間真的重疊」的卡才分排；單獨一張卡維持原本的位置（不留空排）。
- 上下順序 = 先開始的在上排（新來的從下排進場、舊的往上讓）；同時開始才比講者 key。
- 重疊只發生在卡的一部分時，卡會被切成數段事件——沒重疊的那段仍待在原位。

輸出的是「這筆事件要讓開幾行字」（stack_lines）而不是像素；換算成 ASS 每事件
MarginV 的是 stack_offset_px()。讓開的方向由呼叫端的對齊方式決定（YT 底部對齊
往上疊、Reels 畫面中央往下疊），所以 layout() 要知道 bottom_anchored。

另有一道前處理 hold_tail()：真實素材的卡是一張接一張、時間幾乎不重疊，光靠
「重疊才分排」這條規則等於永遠不會觸發（前端預覽也一樣，所以預覽同樣看不到）。
"""
from __future__ import annotations
import bisect

# 一排字幕佔的高度 = 字級 × 這個比例（含描邊與行距留白）。ASS 沒有「行高」欄位，
# 上下排的間距只能靠每事件 MarginV 自己算。
LINE_HEIGHT_RATIO = 1.3

# 兩個用途共用同一個門檻：
# (1) 重疊短於這個秒數不算「同時說話」（分軌合併常見的擦邊重疊，抬起來只會閃一下）
# (2) 分排後不足這個長度的片段，併進相鄰片段（避免字幕上下彈一下）
MIN_STACK_SEC = 0.25

# hold（見 hold_tail）：相鄰兩卡間隔小於這個秒數才算「接話」，超過就當換話題不延長。
HOLD_GAP_SEC = 0.3


def hold_tail(
    cards: list[dict], hold_sec: float, gap_sec: float = HOLD_GAP_SEC
) -> list[dict]:
    """換人接話時把上一句多留在畫面上 hold_sec 秒（回傳新 list，依 start 排序）。

    為什麼需要：字幕來自單軌混音轉錄，卡是一張接一張產生的（間隔 0~0.15 秒），
    講者標是事後貼上去的能量標籤——「兩人同時說話」在資料層面根本不存在。實測
    五集分軌集共 6636 張卡，時間真正重疊的只有 1 筆。不製造重疊，雙行永遠不會觸發。

    被延長的那張永遠是先開始的那張，而 layout 讓先開始的排上面（見它的排序鍵），
    所以延長的效果一律是「上一句往上讓位、讓完就消失」，不會有字幕抬起來又落回原位。

    延長有三道上限，都是為了避免製造出比原本更怪的畫面：
    - 不超過接話那句的結束（否則它消失了上一句還掛著，剩一排孤字）
    - 不撞到同一位講者的下一句（否則同一個人自己占滿上下兩排）
    - 延不出 MIN_STACK_SEC 以上的重疊就整個放棄（抬起來只閃一下，layout 也會併掉）
    """
    ordered = sorted(cards, key=lambda c: (float(c["start"]), float(c["end"])))
    if hold_sec <= 0:
        return [dict(c) for c in ordered]
    out = [dict(c) for c in ordered]
    for i, cur in enumerate(out[:-1]):
        nxt = out[i + 1]
        spk, nxt_spk = cur.get("speaker"), nxt.get("speaker")
        if not spk or not nxt_spk or spk == nxt_spk:
            # 沒講者標 → 分不出是不是換人；同一人接自己 → 不算接話
            continue
        gap = float(nxt["start"]) - float(cur["end"])
        if gap < 0 or gap >= gap_sec:  # 已經重疊 → 不必動；隔太久 → 不是接話
            continue
        cap = float(cur["end"]) + hold_sec
        limit = float(nxt["end"])
        for later in out[i + 2:]:
            if float(later["start"]) >= cap:
                break  # 再後面的卡都碰不到，不影響上限
            if later.get("speaker") == spk:
                limit = min(limit, float(later["start"]))
                break
        new_end = min(cap, limit)
        if new_end - float(nxt["start"]) >= MIN_STACK_SEC:
            cur["end"] = new_end
    return out


def layout(cards: list[dict], bottom_anchored: bool = True) -> list[dict]:
    """帶 speaker 的字幕卡 → 事件清單（依 start 排序）。

    每筆事件 = {start, end, text, speaker, stack_lines}；stack_lines = 這筆要從
    「單行時的位置」讓開幾行字。底部對齊時讓開的是下方的行（最下排 = 0）；
    中央／頂部對齊時讓開的是上方的行（最上排 = 0）。

    一張卡若只有部分時間跟別人重疊，會被切成多筆事件（時間相接、文字相同），
    讓沒重疊的那段留在原位。
    """
    n = len(cards)
    if n == 0:
        return []
    starts = [float(c["start"]) for c in cards]
    ends = [max(float(c["end"]), float(c["start"])) for c in cards]
    nlines = [
        str(c.get("text") or "").replace("\r\n", "\n").count("\n") + 1
        for c in cards
    ]
    # 排序鍵：開始時間 → 講者字典序 → 原卡序（同時開始也要有穩定的上下關係）。
    # 先講的排上面，新來的從下排進場，舊的被往上推。這個順序不只是閱讀習慣：
    # 它讓 stack 只可能隨時間上升，所以一張卡不會抬起來又落回原位（見 hold_tail）。
    keys = [(starts[i], str(cards[i].get("speaker") or ""), i) for i in range(n)]

    # 掃描線：把時間軸切成「同時出現的卡不變」的片段
    pts = sorted({*starts, *ends})
    marks = sorted(
        [(ends[i], 0, i) for i in range(n)] + [(starts[i], 1, i) for i in range(n)]
    )  # 同一時刻先結束再開始 → 前後相接的卡不算重疊
    active: set[int] = set()
    spans: list[tuple[float, float, tuple[int, ...]]] = []
    mi = 0
    for k, t in enumerate(pts):
        while mi < len(marks) and marks[mi][0] <= t:
            _, kind, i = marks[mi]
            if kind == 0:
                active.discard(i)
            else:
                active.add(i)
            mi += 1
        if k + 1 < len(pts):
            spans.append((t, pts[k + 1], tuple(active)))
    span_starts = [s[0] for s in spans]

    out: list[dict] = []
    for i in range(n):
        if ends[i] <= starts[i]:  # 零長度卡：不可能跟人重疊，原樣輸出
            out.append(_event(cards[i], starts[i], ends[i], 0))
            continue
        k0 = bisect.bisect_left(span_starts, starts[i])
        k1 = bisect.bisect_left(span_starts, ends[i])
        slices = []
        for t0, t1, act in spans[k0:k1]:
            order = sorted(act, key=lambda j: keys[j])
            pos = order.index(i)
            hidden = order[pos + 1:] if bottom_anchored else order[:pos]
            slices.append({
                "start": t0, "end": t1, "stack": sum(nlines[j] for j in hidden),
            })
        for s in _merge_same_row(_absorb_tiny(slices)):
            out.append(_event(cards[i], s["start"], s["end"], s["stack"]))
    # 排成「畫面上從上到下」：底部對齊時讓開越多排的越上面，中央/頂部對齊時相反。
    # libass 在垂直置中對齊時會忽略 MarginV、改用自己的碰撞堆疊，那時**檔案順序就是
    # 顯示順序**，排錯會讓上下兩排跟 YT 及編輯器預覽相反。
    out.sort(key=lambda e: (
        e["start"],
        -e["stack_lines"] if bottom_anchored else e["stack_lines"],
        str(e["speaker"] or ""),
    ))
    return out


def stack_offset_px(event: dict, font_size: float) -> int:
    """事件要從「單行時的位置」讓開幾像素（方向已由 layout 的 bottom_anchored 決定）。"""
    return int(round(int(event["stack_lines"]) * float(font_size) * LINE_HEIGHT_RATIO))


def assign_speakers(
    cards: list[dict], spans: list[tuple[float, float, str]]
) -> list[dict]:
    """把 (start, end, speaker) 區間依「時間重疊最多」貼到每張卡上（回傳新 list）。

    刻意不用卡的 idx 對照：出片前的 SRT 可能已被 filter/shift 過，而
    srt_io.serialize 一律從 1 重新編號，idx 對不回 speakers sidecar。
    """
    ordered = sorted((float(a), float(b), str(s)) for a, b, s in spans)
    span_starts = [s[0] for s in ordered]
    out: list[dict] = []
    for c in cards:
        st, en = float(c["start"]), float(c["end"])
        i = bisect.bisect_left(span_starts, st)
        while i > 0 and ordered[i - 1][1] > st:  # 往前收涵蓋 st 的長區間
            i -= 1
        best, best_ov = None, 0.0
        while i < len(ordered) and ordered[i][0] < en:
            ov = min(en, ordered[i][1]) - max(st, ordered[i][0])
            if ov > best_ov:
                best, best_ov = ordered[i][2], ov
            i += 1
        out.append({**c, "speaker": best})
    return out


def _event(card: dict, start: float, end: float, stack: int) -> dict:
    return {
        "start": start,
        "end": end,
        "text": card.get("text") or "",
        "speaker": card.get("speaker"),
        "stack_lines": stack,
    }


def _absorb_tiny(slices: list[dict]) -> list[dict]:
    """把短於 MIN_STACK_SEC 的片段併進較長的鄰居（整張卡只有一段時不動它）。"""
    out = [dict(s) for s in slices]
    while len(out) > 1:
        i = min(range(len(out)), key=lambda k: out[k]["end"] - out[k]["start"])
        if out[i]["end"] - out[i]["start"] >= MIN_STACK_SEC:
            break
        left = out[i - 1] if i > 0 else None
        right = out[i + 1] if i + 1 < len(out) else None
        take_right = left is None or (
            right is not None
            and (right["end"] - right["start"]) > (left["end"] - left["start"])
        )
        if take_right:
            right["start"] = out[i]["start"]
        else:
            left["end"] = out[i]["end"]
        out.pop(i)
    return out


def _merge_same_row(slices: list[dict]) -> list[dict]:
    """相鄰且落在同一排的片段併回一筆（同一排不必拆成兩個事件）。"""
    out: list[dict] = []
    for s in slices:
        prev = out[-1] if out else None
        if (
            prev is not None
            and prev["stack"] == s["stack"]
            and abs(prev["end"] - s["start"]) < 1e-9
        ):
            prev["end"] = s["end"]
        else:
            out.append(dict(s))
    return out
