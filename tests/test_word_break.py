"""word_break：jieba 詞界評分斷句引擎（有/無 jieba 兩態）。"""
from __future__ import annotations

from podcast_toolkit import word_break


def _split_text(s: str, max_w: float = 16.0) -> list[str]:
    chars = [(c, float(i), float(i + 1)) for i, c in enumerate(s)]
    return ["".join(s[a:b]) for a, b in word_break.balanced_split(chars, max_w)]


# ---- 有 jieba（系統已裝 0.42.1）----

def test_short_text_single_piece():
    assert _split_text("大家好") == ["大家好"]


def test_pieces_cover_all_and_within_max():
    s = "他們家的隔音跟通風設備真的做得很不錯然後我們就決定租下來了"
    pieces = _split_text(s)
    assert "".join(pieces) == s
    assert all(word_break.text_width(p) <= 16.0 for p in pieces)


def test_no_word_straddle_common_words():
    """跨卡切詞根因案例：然/後、耳/機 不可被切開。"""
    cases = {
        "他們家的隔音跟通風設備真的做得很不錯然後我們就決定租下來了": "然後",
        "因為現場真的太吵的時候你根本聽不見耳機裡的聲音": "耳機",
    }
    for s, word in cases.items():
        pieces = _split_text(s)
        assert any(word in p for p in pieces), pieces   # 詞必須完整留在某一片
        for a, b in zip(pieces, pieces[1:]):
            assert a[-1] + b[0] != word, pieces         # 不可剛好切在詞中間


def test_no_dangling_conjunction_tail():
    """非最後一片不可以掛尾連接詞（然後/所以…）收尾。"""
    s = "那一場的觀眾少說有五百人甚至可能快要一千人了"
    pieces = _split_text(s)
    for p in pieces[:-1]:
        assert p[-2:] not in word_break.DANGLE_TAIL2, pieces
        assert p[-1] != "於", pieces


def test_add_words_registers_custom_word():
    """glossary 詞（來賓藝名）進 jieba 後不可被視為詞中可切。"""
    word_break.add_words(["郝爾蒙斯"])
    pts = word_break.word_break_ok("我是郝爾蒙斯啦")
    assert pts is not None
    # 「郝爾蒙斯」佔 index 2..6：邊界 2、6 在，內部 3/4/5 不在
    assert 2 in pts and 6 in pts
    assert not {3, 4, 5} & pts


# ---- 無 jieba（monkeypatch 模擬缺席）----

def test_word_break_ok_none_without_jieba(monkeypatch):
    monkeypatch.setattr(word_break, "_JIEBA", False)
    assert word_break.word_break_ok("大家好") is None
    assert word_break.available() is False


def test_balanced_split_degrades_without_jieba(monkeypatch):
    """jieba 缺席：詞界懲罰跳過，但仍要正確切段、內容不丟、不超上限。"""
    monkeypatch.setattr(word_break, "_JIEBA", False)
    s = "他們家的隔音跟通風設備真的做得很不錯然後我們就決定租下來了"
    pieces = _split_text(s)
    assert "".join(pieces) == s
    assert len(pieces) >= 2
    assert all(word_break.text_width(p) <= 16.0 for p in pieces)


def test_add_words_without_jieba_no_crash(monkeypatch):
    monkeypatch.setattr(word_break, "_JIEBA", False)
    word_break.add_words(["某來賓"])   # 不可 raise
