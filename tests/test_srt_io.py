"""srt_io：解析 srt 為 cards、序列化 cards 為 srt。"""
import pytest
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
