"""srt_io：解析 srt 為 cards、序列化 cards 為 srt。"""
from podcast_toolkit import srt_io


SAMPLE = """\
1
00:00:00,000 --> 00:00:04,200
大家好

2
00:00:04,200 --> 00:00:12,000
今天聊乳牛
"""


def test_parse_returns_cards_in_order():
    cards = srt_io.parse(SAMPLE)
    assert len(cards) == 2
    assert cards[0] == {"idx": 1, "start": 0.0, "end": 4.2, "text": "大家好"}
    assert cards[1]["idx"] == 2
    assert cards[1]["text"] == "今天聊乳牛"


def test_parse_handles_multiline_text():
    text = "1\n00:00:00,000 --> 00:00:01,000\n第一行\n第二行\n"
    cards = srt_io.parse(text)
    assert cards[0]["text"] == "第一行\n第二行"


def test_serialize_roundtrips():
    cards = srt_io.parse(SAMPLE)
    assert srt_io.serialize(cards).strip() == SAMPLE.strip()


def test_serialize_applies_text_overrides():
    cards = srt_io.parse(SAMPLE)
    out = srt_io.serialize(cards, overrides={1: "大家午安"})
    assert "大家午安" in out
    assert "大家好" not in out
    # 第 2 段未動
    assert "今天聊乳牛" in out


def test_parse_skips_blank_blocks():
    text = "\n\n1\n00:00:00,000 --> 00:00:01,000\nA\n\n\n"
    assert len(srt_io.parse(text)) == 1


# ---------- splits（前端 Enter 切分後送來）----------

def test_serialize_splits_card_into_two_with_renumber():
    cards = srt_io.parse(SAMPLE)
    out = srt_io.serialize(cards, splits={2: ["今天聊", "乳牛"]})
    # 1 維持不變、原 2 被切成新 2 / 3
    assert "1\n00:00:00,000 --> 00:00:04,200\n大家好" in out
    assert "\n2\n" in out
    assert "今天聊" in out
    assert "\n3\n" in out
    assert "乳牛" in out
    # 應該總共 3 段
    lines = [l for l in out.strip().split("\n") if l.strip().isdigit()]
    assert lines == ["1", "2", "3"]


def test_serialize_splits_packs_tight_when_card_has_trailing_silence():
    """原卡 4.2 → 12.0（dur=7.8），但「今天聊乳牛」5 字 × 0.3s/字 = 1.5s budget；
    dur 遠大於 budget → sub-cards 從 t0 緊湊排、尾段不分配字幕。
    「今天聊」(3 字) = 0.9s → 4.2 → 5.1；「乳牛」(2 字) = 0.6s → 5.1 → 5.7。
    剩 5.7→12.0 (6.3s) 不指派字幕，避免 sub-card 1 被推進靜音裡。
    """
    cards = srt_io.parse(SAMPLE)
    out = srt_io.serialize(cards, splits={2: ["今天聊", "乳牛"]})
    assert "00:00:04,200 --> 00:00:05,100" in out
    assert "00:00:05,100 --> 00:00:05,700" in out


def test_serialize_splits_falls_back_to_proportional_when_tight():
    """原卡很短（5 字裝在 1s 內），budget 1.5s > dur 1.0s → 退回比例分配貼滿整段。"""
    text = "1\n00:00:00,000 --> 00:00:01,000\n今天聊乳牛\n"
    cards = srt_io.parse(text)
    out = srt_io.serialize(cards, splits={1: ["今天聊", "乳牛"]})
    # 3/5 與 2/5
    assert "00:00:00,000 --> 00:00:00,600" in out
    assert "00:00:00,600 --> 00:00:01,000" in out


def test_serialize_splits_ignores_single_segment():
    """splits 只給 1 段視為沒切：走 else 分支、文字維持原句、idx 不重編。"""
    cards = srt_io.parse(SAMPLE)
    text, idx_map = srt_io.serialize_with_map(cards, splits={2: ["不會被當 override"]})
    assert idx_map == [(1, 0), (2, 0)]
    assert "今天聊乳牛" in text
    assert "不會被當 override" not in text


def test_serialize_with_map_returns_composite_lookup():
    cards = srt_io.parse(SAMPLE)
    _, idx_map = srt_io.serialize_with_map(cards, splits={2: ["前", "後"]})
    # 新 idx 1 = 原 (1,0)；新 idx 2 = 原 (2,0)；新 idx 3 = 原 (2,1)
    assert idx_map == [(1, 0), (2, 0), (2, 1)]


def test_serialize_splits_with_overrides_on_other_card():
    """同時送 overrides[1] 改文字 + splits[2] 切第 2 卡 → 兩個都生效，序號連續。"""
    cards = srt_io.parse(SAMPLE)
    out = srt_io.serialize(
        cards, overrides={1: "改過的第一句"}, splits={2: ["a", "b"]}
    )
    assert "改過的第一句" in out
    assert "\n2\n" in out and "\n3\n" in out
