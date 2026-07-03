"""Breeze 匯入後的字幕清理：講者平滑 + 去甩尾。

兩個在真機（留白計畫）驗證有效、源頭結構性的問題：

1. **講者平滑（smooth_speakers）**：逐卡麥能量 argmax 標講者，遇到句尾能量弱 / 別人麥
   收到串音時，那張短卡會翻錯標 → 同一人被切成不同講者（也害自動鏡頭把連續段切斷）。
   把「夾在同一講者中間、短於 blip_sec 秒的講者 blip」併回較長鄰段的講者。

2. **去甩尾（destrand_cards）**：斷句把修飾語（…的/得/地）後的名詞甩到下一卡開頭
   （「累積的 | 量能…」），讀起來像「卡開頭是上一句的最後兩個字」。把「開頭=≤max_lead 字
   + 空格 + 還有後文」的甩尾，搬回前一張同講者卡。

3. **依語句重切（reflow_by_phrases，接在 proofread 之後）**：proofread 加的空格才是真語句
   邊界。對「連續(gap<0.3)、同講者」的卡，用空格當邊界重切，長串再交給 word_break 的
   jieba 詞界評分引擎切（不在詞中間切「然/後」「耳/機」）。**只在「空格兩側都是中文字」時
   才斷** → 英/數旁的空格（line pay / for 林口 / 東門町1923）自動視為詞內、不拆。
   保守化：一個 run 若「沒有可用空格邊界、也沒有任何卡超過 max_w」→ 原卡原時間直接保留
   （不重併重切），保住 Breeze 逐字時間精度。會改卡數，故另存 speakers。

smooth/destrand 不需重轉、卡數不變；reflow 會重編卡（speakers 一併重對）。沒問題的集近 no-op。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path


def _is_cjk(ch: str) -> bool:
    return "㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿"


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


def _subsplit(chars: list, max_w: int) -> list[tuple[int, int]]:
    """單一語句（無 CJK 空格）超過 max_w → 交給 word_break 評分引擎切成 ≤max_w 段。

    評分涵蓋既有保護：英/數混排詞（胚 pae / 很多 idea / line pay）內部不切、
    「的/得/地」不當句首（NO_START）、掛尾連接詞重罰；有 jieba 時再加
    「非詞界重罰」→ 不會切在「然/後」「耳/機」這類詞中間（舊裸掃描的根因）。
    jieba 缺席時詞界懲罰跳過，等同降級到純規則評分。
    寬度尺沿用 toolkit 慣例：每字元（含空格/ascii）算 1，對齊既有 max_w 語意。"""
    from podcast_toolkit import word_break
    return word_break.balanced_split(chars, max_w=float(max_w),
                                     width=lambda c: 1.0)


def _has_cjk_space(text: str) -> bool:
    """文字內是否有「兩側皆中文字」的空格（＝proofread 加的可用語句邊界）。"""
    for k in range(1, len(text) - 1):
        if text[k] == " " and _is_cjk(text[k - 1]) and _is_cjk(text[k + 1]):
            return True
    return False


def reflow_by_phrases(
    cards: list[dict],
    speakers: dict[int, str],
    *,
    gap: float = 0.3,
    max_w: int = 16,
) -> tuple[list[dict], dict[int, str]]:
    """對「連續(間隔<gap)、同講者」的卡，用 proofread 空格當語句邊界重切。

    只在「空格兩側都是中文字」處斷句 → 英/數旁的空格（line pay / for 林口 / 東門町1923）
    自動當詞內、不拆。單一語句>max_w 才再交 word_break 評分切。時間在原卡內線性插值。
    保守化：run 內「無可用空格邊界、且無任何卡超過 max_w」→ 原卡原時間直接保留
    （只重編 idx），不重併重切，保住 Breeze 逐字時間精度。
    回傳 (新 cards[idx 從 1 重編], 新 speakers)；停頓分開的卡（真邊界）不會被併。
    """
    ordered = sorted(cards, key=lambda c: float(c["start"]))
    runs: list[list[dict]] = []
    for c in ordered:
        sp = speakers.get(int(c["idx"]))
        if (runs and speakers.get(int(runs[-1][-1]["idx"])) == sp
                and float(c["start"]) - float(runs[-1][-1]["end"]) < gap):
            runs[-1].append(c)
        else:
            runs.append([c])

    new_cards: list[dict] = []
    new_spk: dict[int, str] = {}
    nid = 0
    for run in runs:
        sp = speakers.get(int(run[0]["idx"]))
        texts = [(c["text"] or "").replace("\n", " ") for c in run]
        # 保守化：沒有可用的 proofread 空格邊界、也沒有任何卡超過 max_w
        # → 這個 run 不重併重切，原卡原時間保留（只重編 idx / speakers 對齊）
        if (not any(_has_cjk_space(t) for t in texts)
                and not any(len(t) > max_w for t in texts)):   # 尺同 _subsplit：每字元算 1
            for c, t in zip(run, texts):
                t = re.sub(r"\s+", " ", t).strip()
                if not t:
                    continue
                nid += 1
                new_cards.append({"idx": nid, "start": float(c["start"]),
                                  "end": float(c["end"]), "text": t})
                if sp:
                    new_spk[nid] = sp
            continue
        chars: list[tuple] = []
        for c in run:
            t = (c["text"] or "").replace("\n", " ")
            if not t:
                continue
            d = (float(c["end"]) - float(c["start"])) / len(t)
            for k, ch in enumerate(t):
                chars.append((ch, float(c["start"]) + d * k, float(c["start"]) + d * (k + 1)))
        # 用「兩側皆中文字」的空格切語句；其餘空格（英/數旁）留在語句內
        phrases: list[list] = []
        cur: list = []
        for k, (ch, s, e) in enumerate(chars):
            if ch == " ":
                prev_ch = chars[k - 1][0] if k > 0 else ""
                next_ch = chars[k + 1][0] if k + 1 < len(chars) else ""
                if cur and _is_cjk(prev_ch) and _is_cjk(next_ch):
                    phrases.append(cur)
                    cur = []
                    continue
            cur.append((ch, s, e))
        if cur:
            phrases.append(cur)
        for ph in phrases:
            for a, b in _subsplit(ph, max_w):
                txt = re.sub(r"\s+", " ", "".join(x[0] for x in ph[a:b])).strip()
                if not txt:
                    continue
                nid += 1
                new_cards.append({"idx": nid, "start": ph[a][1],
                                  "end": ph[b - 1][2], "text": txt})
                if sp:
                    new_spk[nid] = sp
    return new_cards, new_spk


def reflow_episode(episode_dir, *, gap: float = 0.3) -> int:
    """讀 _final_v2.srt + speakers.json → 依語句重切 → 寫回（先備份 .pre-reflow.bak）。

    接在 proofread 之後跑（需 proofread 加的空格）。回傳重切後卡數；無 _v2 → 0。
    """
    from podcast_toolkit import cameras_io, srt_io, word_break
    from podcast_toolkit.episode import Episode

    ep = Episode(Path(episode_dir))
    # jieba 詞典可由 resegment.jieba_dict 覆寫；episode glossary（來賓名等）進詞典避免被切開
    word_break.configure((ep.cfg.get("resegment") or {}).get("jieba_dict"))
    word_break.add_words([g.get("canonical") for g in (ep.cfg.get("glossary") or [])])
    v2 = ep.output_v2_srt()
    spk_path = ep.output_v2_speakers_json()
    if not v2.exists():
        return 0
    cards = srt_io.parse(v2.read_text(encoding="utf-8"))
    speakers = cameras_io.load(spk_path)
    new_cards, new_spk = reflow_by_phrases(cards, speakers, gap=gap)
    if not new_cards:
        return 0
    shutil.copy(v2, v2.with_name(f"{v2.stem}.pre-reflow.bak{v2.suffix}"))
    if spk_path.exists():
        shutil.copy(spk_path, spk_path.with_name(spk_path.name + ".pre-reflow.bak"))
    v2.write_text(srt_io.serialize(new_cards), encoding="utf-8")
    cameras_io.save(spk_path, new_spk)
    return len(new_cards)
