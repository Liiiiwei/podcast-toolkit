"""Breeze 匯入後的字幕清理：講者平滑 + 去甩尾。

兩個在真機（留白計畫）驗證有效、源頭結構性的問題：

1. **講者平滑（smooth_speakers）**：逐卡麥能量 argmax 標講者，遇到句尾能量弱 / 別人麥
   收到串音時，那張短卡會翻錯標 → 同一人被切成不同講者（也害自動鏡頭把連續段切斷）。
   把「夾在同一講者中間、短於 blip_sec 秒的講者 blip」併回較長鄰段的講者。

2. **去甩尾（destrand_cards）**：斷句把修飾語（…的/得/地）後的名詞甩到下一卡開頭
   （「累積的 | 量能…」），讀起來像「卡開頭是上一句的最後兩個字」。把「開頭=≤max_lead 字
   + 空格 + 還有後文」的甩尾，搬回前一張同講者卡。

兩者都不需重新轉錄；卡數 / idx 不變（speakers.json 仍對齊）。對沒有問題的集是 no-op。
"""
from __future__ import annotations


def smooth_speakers(
    cards: list[dict],
    speakers: dict[int, str],
    *,
    blip_sec: float = 2.0,
) -> dict[int, str]:
    """把短於 blip_sec 的講者 blip 併回較長鄰段的講者。回傳新的 {idx: speaker}；cards 不變。

    反覆挑「時長最短、且兩側鄰段講者不同的」blip 段，整段改貼成時長較長那側的講者，
    直到沒有可併的短段。speakers 為空（無講者集）→ 原樣回傳。
    """
    if not speakers:
        return dict(speakers)
    ordered = sorted(cards, key=lambda c: float(c["start"]))
    labels: dict[int, str | None] = {
        int(c["idx"]): speakers.get(int(c["idx"])) for c in ordered
    }

    def _segments() -> list[dict]:
        segs: list[dict] = []
        for c in ordered:
            lab = labels.get(int(c["idx"]))
            if segs and segs[-1]["lab"] == lab:
                segs[-1]["cards"].append(c)
                segs[-1]["end"] = float(c["end"])
            else:
                segs.append({"lab": lab, "cards": [c],
                             "start": float(c["start"]), "end": float(c["end"])})
        return segs

    while True:
        segs = _segments()
        target = None
        tdur = None
        for k, s in enumerate(segs):
            if s["lab"] is None:
                continue
            dur = s["end"] - s["start"]
            if dur >= blip_sec:
                continue
            left = segs[k - 1] if k > 0 else None
            right = segs[k + 1] if k < len(segs) - 1 else None
            if left is None or right is None:
                continue  # 第一/最後段只有單邊鄰居 → 不是夾在中間的 blip，保留
            cands = [x for x in (left, right)
                     if x["lab"] is not None and x["lab"] != s["lab"]]
            if not cands:
                continue
            if tdur is None or dur < tdur:
                tdur = dur
                target = (k, cands)
        if target is None:
            break
        k, cands = target
        best = max(cands, key=lambda x: x["end"] - x["start"])
        for c in segs[k]["cards"]:
            labels[int(c["idx"])] = best["lab"]

    return {idx: lab for idx, lab in labels.items() if lab}


def destrand_cards(
    cards: list[dict],
    speakers: dict[int, str],
    *,
    max_lead: int = 2,
    max_prev_len: int = 20,
) -> list[dict]:
    """把「開頭=≤max_lead 字 + 空格 + 還有後文」的甩尾，搬回前一張同講者卡。

    就地改 cards 的 text / start / end（時間在原卡內線性插值取切點）；
    卡數與 idx 不變，speakers 仍對齊。回傳同一個 cards list。
    """
    ordered = sorted(cards, key=lambda c: float(c["start"]))
    for i in range(1, len(ordered)):
        cur, prev = ordered[i], ordered[i - 1]
        if speakers.get(int(cur["idx"])) != speakers.get(int(prev["idx"])):
            continue                                  # 不同講者不挪
        parts = cur["text"].split(" ", 1)
        if len(parts) != 2:
            continue
        lead, rest = parts[0], parts[1].strip()
        if not lead or not rest or len(lead) > max_lead:
            continue                                  # 開頭詞要短、且後面還有字
        if len(prev["text"]) + len(lead) > max_prev_len:
            continue                                  # 前卡太長就不硬塞
        full = cur["text"]
        frac = (len(lead) + 1) / max(1, len(full))    # lead+空格 佔的時間比 → 切點
        split_t = float(cur["start"]) + frac * (float(cur["end"]) - float(cur["start"]))
        prev["text"] = prev["text"] + lead            # 句尾接回前卡（無空格＝連續）
        prev["end"] = split_t
        cur["text"] = rest
        cur["start"] = split_t
    return cards
