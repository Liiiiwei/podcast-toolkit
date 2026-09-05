"""字幕最後拋光：修閃字幕、超長停留、時間重疊，並產生驗證報告。

這層接在 proofread + reflow 之後。它只做可由時間戳判斷的保守修復；
語意錯字仍交給 proofread 與人工詞庫，避免跨集盲替換誤傷。
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from podcast_toolkit import cameras_io, srt_io
from podcast_toolkit.episode import Episode
from podcast_toolkit.fsutil import atomic_write_text

MIN_FLASH_SEC = 0.30
SHORT_REVIEW_SEC = 0.80
MAX_HOLD_SEC = 6.00
MAX_CHARS = 26
MAX_CPS = 13.0
MIN_SINGLE_CHAR = 1

REACTION_WORDS = frozenset({
    "好", "對", "對啊", "對呀", "對對", "對對對", "嗯", "嗯嗯", "沒有",
    "真的", "真的嗎", "是喔", "是吧", "哇", "蛤", "喔", "哦", "哈哈",
})
FORCE_ATTACH_PREFIXES = ("的", "了", "著", "過", "事情", "概念")


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _disp_len(text: str) -> int:
    return len(_clean_text(text))


def _is_single_or_reaction(text: str) -> bool:
    compact = _clean_text(text)
    return len(compact) <= MIN_SINGLE_CHAR or compact in REACTION_WORDS


def _load_words(ep: Episode) -> list[dict[str, Any]]:
    """讀 Breeze word cache；沒有就回空清單。"""
    candidates = [
        ep.dir / f"{ep.dir.name}_字幕_words.json",
        ep.dir / f"{ep.name}_字幕_words.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            words = payload.get("words") or []
            return [w for w in words if isinstance(w, dict)]
        except Exception:
            return []
    return []


def _split_long_card(card: dict, words: list[dict[str, Any]]) -> list[dict]:
    """把超長卡切成兩張；優先使用逐字資料決定邊界。"""
    start, end = float(card["start"]), float(card["end"])
    text = (card.get("text") or "").strip()
    if not text:
        return [card]
    chars = list(text)
    if len(chars) < 2:
        return [card]
    candidates = []
    for m in re.finditer(r"(或是|還是|但是|可是|所以|因為|然後|就是|那我覺得就是)", text):
        if 0 < m.end() < len(chars):
            candidates.append(m.end())

    word_boundaries: list[tuple[int, float]] = []
    cursor = 0
    compact_text = _clean_text(text)
    compact_words = []
    for word in words:
        value = _clean_text(str(word.get("word") or word.get("text") or ""))
        if not value:
            continue
        compact_words.append((value, float(word["start"]), float(word["end"])))
    if "".join(word[0] for word in compact_words) == compact_text:
        for value, _, word_end in compact_words[:-1]:
            cursor += len(value)
            word_boundaries.append((cursor, word_end))

    if word_boundaries:
        target = len(chars) / 2
        pos, cut_t = min(word_boundaries, key=lambda item: (abs(item[0] - target), item[0]))
        if pos > 0 and pos < len(chars):
            return [
                {"idx": card["idx"], "start": start, "end": cut_t, "text": "".join(chars[:pos]).strip()},
                {"idx": card["idx"], "start": cut_t, "end": end, "text": "".join(chars[pos:]).strip()},
            ]

    if not candidates:
        candidates = [len(chars) // 2]
    pos = max(1, min(len(chars) - 1, min(candidates, key=lambda x: abs(x - len(chars) / 2))))
    cut_t = start + (end - start) * (pos / len(chars))
    return [
        {"idx": card["idx"], "start": start, "end": cut_t, "text": "".join(chars[:pos]).strip()},
        {"idx": card["idx"], "start": cut_t, "end": end, "text": "".join(chars[pos:]).strip()},
    ]


def _renumber(cards: list[dict], speakers: dict[int, str]) -> tuple[list[dict], dict[int, str]]:
    new_cards: list[dict] = []
    new_spk: dict[int, str] = {}
    for new_idx, card in enumerate(sorted(cards, key=lambda c: float(c["start"])), 1):
        old_idx = int(card.get("idx", new_idx))
        new_cards.append({
            "idx": new_idx,
            "start": float(card["start"]),
            "end": float(card["end"]),
            "text": str(card.get("text") or "").strip(),
        })
        sp = speakers.get(old_idx)
        if sp:
            new_spk[new_idx] = sp
    return new_cards, new_spk


def _merge_flash_cards(cards: list[dict], speakers: dict[int, str]) -> tuple[list[dict], dict[int, str], int]:
    """合併小於 0.3 秒且非單字/反應詞的卡。"""
    ordered = sorted((dict(c) for c in cards), key=lambda c: float(c["start"]))
    out: list[dict] = []
    merged = 0
    i = 0
    while i < len(ordered):
        card = ordered[i]
        dur = float(card["end"]) - float(card["start"])
        if dur >= MIN_FLASH_SEC or _is_single_or_reaction(str(card.get("text") or "")):
            out.append(card)
            i += 1
            continue
        cur_text = str(card.get("text") or "").strip()
        if out:
            prev = out[-1]
            same_sp = not speakers or speakers.get(int(prev["idx"])) == speakers.get(int(card["idx"]))
            gap = float(card["start"]) - float(prev["end"])
            if same_sp and 0 <= gap <= SHORT_REVIEW_SEC:
                prev_text = str(prev.get("text") or "").strip()
                prev["text"] = prev_text + cur_text
                prev["end"] = float(card["end"])
                merged += 1
                i += 1
                continue
        if i + 1 < len(ordered):
            nxt = ordered[i + 1]
            same_sp = not speakers or speakers.get(int(nxt["idx"])) == speakers.get(int(card["idx"]))
            gap = float(nxt["start"]) - float(card["end"])
            if same_sp and 0 <= gap <= SHORT_REVIEW_SEC:
                nxt = dict(nxt)
                nxt["start"] = float(card["start"])
                nxt["text"] = cur_text + str(nxt.get("text") or "").strip()
                ordered[i + 1] = nxt
                merged += 1
                i += 1
                continue
        out.append(card)
        i += 1
    new_cards, new_spk = _renumber(out, speakers)
    return new_cards, new_spk, merged


def _split_problem_cards(
    cards: list[dict],
    speakers: dict[int, str],
    words: list[dict[str, Any]],
) -> tuple[list[dict], dict[int, str], int]:
    out: list[dict] = []
    splits = 0
    pending = [dict(card) for card in sorted(cards, key=lambda c: float(c["start"]))]
    while pending:
        card = pending.pop(0)
        dur = float(card["end"]) - float(card["start"])
        chars = _disp_len(str(card.get("text") or ""))
        cps = chars / dur if dur > 0 else 999.0
        should_split = dur > MAX_HOLD_SEC or chars > MAX_CHARS or (dur >= SHORT_REVIEW_SEC and cps > MAX_CPS)
        if should_split:
            pieces = [p for p in _split_long_card(card, words) if str(p.get("text") or "").strip()]
            if len(pieces) > 1:
                pending[0:0] = pieces
                splits += 1
                continue
        out.append(card)
    new_cards, new_spk = _renumber(out, speakers)
    return new_cards, new_spk, splits


def analyze(cards: list[dict]) -> dict[str, Any]:
    errors = []
    prev_end = -1.0
    short_flash = []
    short_review = []
    long_hold = []
    long_chars = []
    fast = []
    for expected, card in enumerate(sorted(cards, key=lambda c: float(c["start"])), 1):
        idx = int(card.get("idx", expected))
        start, end = float(card["start"]), float(card["end"])
        text = str(card.get("text") or "").strip()
        dur = end - start
        chars = _disp_len(text)
        cps = chars / dur if dur > 0 else 999.0
        if idx != expected:
            errors.append({"idx": idx, "type": "bad_index"})
        if end <= start:
            errors.append({"idx": idx, "type": "non_positive_duration"})
        if start < prev_end - 0.001:
            errors.append({"idx": idx, "type": "overlap"})
        prev_end = max(prev_end, end)
        item = {"idx": idx, "dur": round(dur, 3), "chars": chars, "cps": round(cps, 1), "text": text}
        if dur < MIN_FLASH_SEC and not _is_single_or_reaction(text):
            short_flash.append(item)
        if dur < SHORT_REVIEW_SEC and not _is_single_or_reaction(text):
            short_review.append(item)
        if dur > MAX_HOLD_SEC:
            long_hold.append(item)
        if chars > MAX_CHARS:
            long_chars.append(item)
        if cps > MAX_CPS:
            fast.append(item)
    all_text = "\n".join(str(c.get("text") or "") for c in cards)
    return {
        "cards": len(cards),
        "errors": errors,
        "short_flash": short_flash,
        "short_review": short_review,
        "long_hold": long_hold,
        "long_chars": long_chars,
        "fast": fast,
        "has_japanese_kana": bool(re.search(r"[あ-んア-ン]", all_text)),
        "simplified_candidates": [c for c in ("后", "发") if c in all_text],
    }


def _write_report(ep: Episode, before: dict[str, Any], after: dict[str, Any], *, merged: int, splits: int) -> Path:
    path = ep.subdir("work") / "_subtitle_polish_report.json"
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "thresholds": {
            "min_flash_sec": MIN_FLASH_SEC,
            "short_review_sec": SHORT_REVIEW_SEC,
            "max_hold_sec": MAX_HOLD_SEC,
            "max_chars": MAX_CHARS,
            "max_cps": MAX_CPS,
        },
        "changes": {"merged_flash_cards": merged, "split_problem_cards": splits},
        "before": before,
        "after": after,
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def run(episode_dir: Path | str, *, force: bool = True) -> dict[str, Any]:
    """拋光 _final_v2.srt。回傳報告摘要；無字幕時回 skipped。"""
    ep = Episode(Path(episode_dir))
    v2 = ep.output_v2_srt()
    if not v2.exists():
        return {"ok": True, "skipped": True, "reason": "找不到 _final_v2.srt"}

    cards = srt_io.parse(v2.read_text(encoding="utf-8"))
    if not cards:
        return {"ok": False, "skipped": True, "reason": "_final_v2.srt 解析為空"}
    spk_path = ep.output_v2_speakers_json()
    speakers = cameras_io.load(spk_path)
    before = analyze(cards)

    words = _load_words(ep)
    new_cards, new_spk, merged = _merge_flash_cards(cards, speakers)
    new_cards, new_spk, splits = _split_problem_cards(new_cards, new_spk, words)
    after = analyze(new_cards)

    quality_keys = ("short_flash", "short_review", "long_hold", "long_chars", "fast")
    quality_ok = not after["errors"] and not any(after[key] for key in quality_keys)
    changed = merged > 0 or splits > 0 or len(new_cards) != len(cards)
    if changed and force and quality_ok:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = v2.with_name(f"{v2.stem}.{stamp}.pre-polish.bak{v2.suffix}")
        shutil.copy(v2, bak)
        if spk_path.exists():
            shutil.copy(spk_path, spk_path.with_name(f"{spk_path.stem}.{stamp}.pre-polish.bak{spk_path.suffix}"))
        atomic_write_text(v2, srt_io.serialize(new_cards))
        cameras_io.save(spk_path, new_spk)

    report = _write_report(ep, before, after, merged=merged, splits=splits)
    return {
        "ok": quality_ok,
        "changed": changed,
        "written": bool(changed and force and quality_ok),
        "cards": after["cards"],
        "merged_flash_cards": merged,
        "split_problem_cards": splits,
        "report": str(report.relative_to(ep.dir)),
        "after": {
            "errors": len(after["errors"]),
            "short_flash": len(after["short_flash"]),
            "short_review": len(after["short_review"]),
            "long_hold": len(after["long_hold"]),
            "long_chars": len(after["long_chars"]),
            "fast": len(after["fast"]),
            "has_japanese_kana": after["has_japanese_kana"],
            "simplified_candidates": after["simplified_candidates"],
        },
    }
