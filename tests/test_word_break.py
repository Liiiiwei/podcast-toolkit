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


# ---- 繁體詞典缺口補丁（_TRAD_FIX）----

def test_trad_fix_words_are_single_tokens():
    """dict.txt.big 缺的繁體常用詞（啟發/認為/開啟…）補進後恆為單一 token，不被亂拆。

    未補時 jieba 把「啟發」拆成 啟｜發（因 啟發 FREQ=None），會讓非詞界重罰失準、
    seg_check 誤報跨卡切詞。補丁後 啟發 自成一詞，邊界乾淨。
    """
    pts = word_break.word_break_ok("我覺得很有啟發")
    assert pts is not None
    # 「啟發」佔 index 5..7：邊界 5、7 在，內部 6 不在（沒被切在詞中間）
    assert 5 in pts and 7 in pts and 6 not in pts
    for w in ["認為", "啟發", "啟蒙", "啟示", "開啟", "啟動", "啟用"]:
        seg = word_break.word_break_ok("他" + w + "了")
        assert seg is not None
        assert 1 in seg and 1 + len(w) in seg, (w, seg)   # 詞界完整、內部不切
        assert not set(range(2, 1 + len(w))) & seg, (w, seg)


def test_domain_gap_word_kept_whole_in_context():
    """專業常用詞缺口（球評）補進後，在整句上下文中也恆為單一 token。

    dict.txt.big 缺「球評」（球賽評論員）→ 未補時 jieba 在句中把它拆成 球｜評 並黏出
    垃圾 token「評要」（就是/所謂/的/球/評要/…），使 balanced_split 切在 球｜評、
    heal_straddle 又因 context 詞界看似乾淨而漏修（魁哥集 422/423 實例）。補進後恆整詞。
    """
    seg = word_break.word_break_ok("就是所謂的球評要測試一下")
    assert seg is not None
    # 「球評」佔 index 5..7：邊界 5、7 在，內部 6 不在（沒被切在詞中間）
    assert 5 in seg and 7 in seg, seg
    assert 6 not in seg, seg
    # 「一開始」佔 index 3..6（實例 878/879 被切成 一｜開始）：邊界 3、6 在，內部 4/5 不在
    seg2 = word_break.word_break_ok("的很難一開始還有一些")
    assert seg2 is not None
    assert 3 in seg2 and 6 in seg2, seg2
    assert not {4, 5} & seg2, seg2
    # 「國中」佔 index 5..7（dict FREQ=3 過低被 國＋中 蓋過，實例 932/933 切成 國｜中）
    seg3 = word_break.word_break_ok("也才差不多國中高中他們")
    assert seg3 is not None
    assert 5 in seg3 and 7 in seg3, seg3
    assert 6 not in seg3, seg3


# ---- 成語整體保留（_IDIOM_KEEP）----

def test_idiom_kept_whole_not_split_internally():
    """登記的成語（啟發人心）恆為單一 token，內部不可斷 → 不會被切在成語中間。

    「啟發人心」是使用者點名的碎卡案例（啟發｜人心 被切在兩張卡）。登記後 4 字整體成詞，
    balanced_split 不挑成語中間當切點，heal_straddle 也能把已切開的搬回同一卡。
    """
    for w in ["啟發人心"]:
        seg = word_break.word_break_ok("很" + w + "喔")
        assert seg is not None
        assert 1 in seg and 1 + len(w) in seg, (w, seg)   # 成語兩端是詞界
        assert not set(range(2, 1 + len(w))) & seg, (w, seg)   # 內部皆不可斷
