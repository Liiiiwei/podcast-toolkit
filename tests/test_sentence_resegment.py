"""逐字→句卡斷句器(sentence_resegment)測試。"""
from __future__ import annotations

from podcast_toolkit import sentence_resegment as sr


def _chars(spec):
    """spec: list of (text, start, end) → char_cards。"""
    return [{"idx": i + 1, "text": t, "start": s, "end": e} for i, (t, s, e) in enumerate(spec)]


def test_normalize_text_comma_to_space_period_removed():
    assert sr.normalize_text(list("整理嗎？其實我是自然卷。")) == "整理嗎 其實我是自然卷"
    assert sr.normalize_text(list("很醜，哎，現在都時髦小物。")) == "很醜 哎 現在都時髦小物"


def test_breaks_at_sentence_ender():
    # 兩句,中間沒大停頓 → 仍應在 。 斷成兩卡
    spec = [(c, i * 0.2, i * 0.2 + 0.2) for i, c in enumerate("你好嗎。我很好。")]
    cards = sr.segment_chars(_chars(spec), maxlen=20, min_chars=1)
    assert [c["text"] for c in cards] == ["你好嗎", "我很好"]


def test_trailing_punct_silence_trimmed_from_timing():
    # 「好」說完在 1.2,後面「。」吸了到 15.94 的靜音 → 卡尾應該是 1.2,不是 15.94
    spec = [("好", 1.0, 1.2), ("。", 1.2, 15.94), ("然", 16.0, 16.1)]
    cards = sr.segment_chars(_chars(spec), maxlen=20, min_chars=1, gapmax=0.6)
    assert cards[0]["text"] == "好"
    assert cards[0]["end"] == 1.2                      # 不是 15.94
    assert cards[1]["text"] == "然"
    assert cards[1]["start"] == 16.0


def test_long_sentence_splits_at_clause_comma():
    # 一句超過 maxlen、中間有逗號 → 切在逗號,不切詞中間
    txt = "今天天氣很好，我們出去玩吧"          # 12 字,逗號在第 6 字後
    spec = [(c, i * 0.3, i * 0.3 + 0.3) for i, c in enumerate(txt)]
    cards = sr.segment_chars(_chars(spec), maxlen=6, min_chars=1, gapmax=5.0)
    texts = [c["text"] for c in cards]
    assert texts == ["今天天氣很好", "我們出去玩吧"]   # 乾淨切在逗號,沒有半個詞


def test_short_card_merged_into_neighbor():
    # 「對」單獨會是短卡,時間連續 → 併進鄰卡,不留一字孤卡
    spec = [(c, i * 0.2, i * 0.2 + 0.2) for i, c in enumerate("對我同意你說的")]
    cards = sr.segment_chars(_chars(spec), maxlen=20, min_chars=3, gapmax=2.0)
    assert all(len(c["text"].replace(" ", "")) >= 3 for c in cards)


def test_short_card_kept_when_isolated_by_gaps():
    # 「對」前後都是大停頓 → 保留(真‧獨立短語,硬併會把卡拉過靜音)
    spec = [("嗯", 0.0, 0.3), ("對", 5.0, 5.3), ("好的", 10.0, 10.5)]
    cards = sr.segment_chars(_chars(spec), maxlen=20, min_chars=3, gapmax=0.6)
    assert any(c["text"] == "對" for c in cards)        # 沒被硬併


def test_idx_renumbered_and_times_monotonic():
    spec = [(c, i * 0.4, i * 0.4 + 0.4) for i, c in enumerate("一二三。四五六。七八九。")]
    cards = sr.segment_chars(_chars(spec), maxlen=20, min_chars=1)
    assert [c["idx"] for c in cards] == list(range(1, len(cards) + 1))
    for a, b in zip(cards, cards[1:]):
        assert a["end"] <= b["start"] + 1e-9            # 不重疊、單調遞增
