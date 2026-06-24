"""講者平滑 + 去甩尾（subtitle_cleanup）測試。"""
from __future__ import annotations

from podcast_toolkit.subtitle_cleanup import (
    destrand_cards, reflow_by_phrases, smooth_speakers,
)


def _cards(*spans):
    """spans: (start, end, text) → cards list（idx 從 1）。"""
    return [{"idx": i, "start": s, "end": e, "text": t}
            for i, (s, e, t) in enumerate(spans, 1)]


# ---- smooth_speakers ----

def test_smooth_merges_short_blip_between_same_speaker():
    """夾在 c 中間的短 blip（b, 0.5s）→ 併回 c。"""
    cards = _cards((0, 3, "x"), (3, 6, "x"), (6, 6.5, "x"), (6.5, 9, "x"), (9, 12, "x"))
    spk = {1: "c", 2: "c", 3: "b", 4: "c", 5: "c"}
    out = smooth_speakers(cards, spk, blip_sec=2.0)
    assert out == {1: "c", 2: "c", 3: "c", 4: "c", 5: "c"}


def test_smooth_merges_consecutive_blips():
    """連續兩個不同 blip（b 再 a）夾在 c 中間 → 兩個都併回 c。"""
    cards = _cards((0, 4, "x"), (4, 5, "x"), (5, 5.8, "x"), (5.8, 10, "x"))
    spk = {1: "c", 2: "b", 3: "a", 4: "c"}
    out = smooth_speakers(cards, spk, blip_sec=2.0)
    assert out == {1: "c", 2: "c", 3: "c", 4: "c"}


def test_smooth_keeps_long_segment():
    """夠長的段（b, 4s ≥ blip_sec）不算 blip → 不動。"""
    cards = _cards((0, 3, "x"), (3, 7, "x"), (7, 10, "x"))
    spk = {1: "c", 2: "b", 3: "c"}
    out = smooth_speakers(cards, spk, blip_sec=2.0)
    assert out == {1: "c", 2: "b", 3: "c"}


def test_smooth_keeps_short_edge_segment():
    """第一段雖短（只有右鄰）不算夾中間的 blip → 保留（避免誤併合法開場短句）。"""
    cards = _cards((0, 1.5, "x"), (1.5, 6, "x"), (6, 10, "x"))
    spk = {1: "a", 2: "b", 3: "c"}
    out = smooth_speakers(cards, spk, blip_sec=2.0)
    assert out == {1: "a", 2: "b", 3: "c"}


def test_smooth_empty_speakers_is_noop():
    assert smooth_speakers(_cards((0, 1, "x")), {}) == {}


# ---- destrand_cards ----

def test_destrand_moves_short_lead_to_prev_same_speaker():
    """後卡「量能 …」開頭 2 字甩尾 → 接回前卡，切點一致。"""
    cards = _cards((0.0, 2.0, "我們會處理"), (2.0, 5.0, "量能 然後呢做這個"))
    spk = {1: "c", 2: "c"}
    destrand_cards(cards, spk)
    assert cards[0]["text"] == "我們會處理量能"
    assert cards[1]["text"] == "然後呢做這個"
    assert cards[0]["end"] == cards[1]["start"]   # 切點一致、時間連續


def test_destrand_skips_different_speaker():
    """前後卡不同講者（可能是真的兩人）→ 不挪。"""
    cards = _cards((0.0, 2.0, "我們會處理"), (2.0, 5.0, "量能 然後呢做這個"))
    spk = {1: "c", 2: "b"}
    destrand_cards(cards, spk)
    assert cards[1]["text"] == "量能 然後呢做這個"   # 原樣


def test_destrand_skips_long_lead():
    """開頭詞 >2 字（不是甩尾，是正常開頭）→ 不挪。"""
    cards = _cards((0.0, 2.0, "我們會處理"), (2.0, 5.0, "量能很多 然後呢"))
    spk = {1: "c", 2: "c"}
    destrand_cards(cards, spk)
    assert cards[1]["text"] == "量能很多 然後呢"


def test_destrand_skips_when_no_rest():
    """整卡就是一個短詞（無後文）= 獨立短回應，不是甩尾 → 不挪。"""
    cards = _cards((0.0, 2.0, "我們會處理"), (2.0, 3.0, "對啊"))
    spk = {1: "c", 2: "c"}
    destrand_cards(cards, spk)
    assert cards[1]["text"] == "對啊"


def test_destrand_cascades_left_to_right():
    """連續甩尾：一次 pass 由左到右各自接回。"""
    cards = _cards((0.0, 2.0, "一切的"), (2.0, 4.0, "起點 那再到"), (4.0, 6.0, "量能 然後"))
    spk = {1: "c", 2: "c", 3: "c"}
    destrand_cards(cards, spk)
    assert cards[0]["text"] == "一切的起點"
    assert cards[1]["text"] == "那再到量能"
    assert cards[2]["text"] == "然後"


# ---- reflow_by_phrases ----

def test_reflow_splits_at_cjk_spaces_joins_across_cards():
    """連續同講者：空格在流行/機器人後切；送餐|機器人(卡邊界無空格)接起來。"""
    cards = _cards((0.0, 3.0, "其實區塊鏈很流行 然後什麼送餐"),
                   (3.0, 6.0, "機器人 那個時候也剛冒出來"))
    new, _ = reflow_by_phrases(cards, {1: "c", 2: "c"}, gap=0.3)
    assert [c["text"] for c in new] == [
        "其實區塊鏈很流行", "然後什麼送餐機器人", "那個時候也剛冒出來"]
    assert [c["idx"] for c in new] == [1, 2, 3]


def test_reflow_protects_ascii_adjacent_spaces():
    """英/數旁的空格不算語句邊界（line pay 不拆）；只有兩中文字間才斷。"""
    cards = _cards((0.0, 4.0, "用 line pay 付款 然後走"))
    new, _ = reflow_by_phrases(cards, {1: "c"})
    assert [c["text"] for c in new] == ["用 line pay 付款", "然後走"]


def test_reflow_different_speaker_not_joined():
    cards = _cards((0.0, 3.0, "我問你"), (3.0, 6.0, "題目"))
    new, ns = reflow_by_phrases(cards, {1: "a", 2: "c"}, gap=0.3)
    assert [c["text"] for c in new] == ["我問你", "題目"]
    assert ns == {1: "a", 2: "c"}


def test_reflow_pause_breaks_run():
    """同講者但間隔 > gap（真停頓）→ 不併。"""
    cards = _cards((0.0, 3.0, "第一句"), (5.0, 8.0, "第二句"))
    new, _ = reflow_by_phrases(cards, {1: "c", 2: "c"}, gap=0.3)
    assert [c["text"] for c in new] == ["第一句", "第二句"]


def test_reflow_subsplit_long_phrase():
    """超過 max_w 的無空格中文串 → 硬切成 ≤max_w，內容不丟。"""
    cards = _cards((0.0, 4.0, "一二三四五六七八九十甲乙丙丁戊己庚辛"))
    new, _ = reflow_by_phrases(cards, {1: "c"}, max_w=16)
    assert all(len(c["text"]) <= 16 for c in new)
    assert "".join(c["text"] for c in new) == "一二三四五六七八九十甲乙丙丁戊己庚辛"
