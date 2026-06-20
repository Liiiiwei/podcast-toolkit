"""模糊字偵測 + curate（glossary_candidates.py）測試。

重點：R7 餵過卻消失要抓到、new_variant 要高精度（不把『專名+助詞』當變體）、
填充詞/已收錄詞不報、寫回 .glossary.json 的 round-trip + dedup、ignore 過濾。
"""
from __future__ import annotations

import json

from podcast_toolkit import config, glossary_candidates as gc


def _cards(texts):
    return [{"idx": i + 1, "start": float(i), "end": float(i) + 1, "text": t}
            for i, t in enumerate(texts)]


def _glos(*entries):
    norm = []
    for e in entries:
        if isinstance(e, str):
            norm.append({"canonical": e, "sounds_like": [], "note": ""})
        else:
            norm.append({"canonical": e[0], "sounds_like": list(e[1]), "note": ""})
    return norm


# ---- R7 餵過卻消失 ----

def test_fed_but_missing_flags_absent_canonical():
    cards = _cards(["今天聊時尚", "印花樂很棒"])
    glos = _glos("印花樂", "茄芷袋", "Wazaiii")
    cands = gc.suggest_candidates(cards, glos, dict_words=set())
    words = {c["word"] for c in cands if c["kind"] == "fed_but_missing"}
    assert "茄芷袋" in words and "Wazaiii" in words   # 不在稿 → 報
    assert "印花樂" not in words                       # 有出現 → 不報


def test_fed_but_missing_covered_by_sounds_like():
    cards = _cards(["我們公司叫印花業"])
    glos = _glos(("印花樂", ["印花業"]))
    cands = gc.suggest_candidates(cards, glos, dict_words=set())
    assert not [c for c in cands if c["kind"] == "fed_but_missing"]  # sounds_like 出現算有


# ---- new_variant 高精度 ----

def test_new_variant_catches_real_mishearing():
    cards = _cards(["就是櫻花樂它", "找到櫻花樂的網站", "印花樂是品牌"])
    glos = _glos("印花樂")
    cands = gc.suggest_candidates(cards, glos, dict_words=set())
    nv = [c for c in cands if c["kind"] == "new_variant"]
    assert any(c["word"] == "櫻花樂" and c["canonical_hint"] == "印花樂" for c in nv)


def test_new_variant_rejects_particle_swap():
    # 印花的/印花這 是『印花』+助詞，不是誤聽，不該報
    cards = _cards(["就是印花的這個", "印花的發展", "印花這邊", "印花這樣"])
    glos = _glos("印花樂")
    cands = gc.suggest_candidates(cards, glos, dict_words=set())
    assert not [c for c in cands if c["kind"] == "new_variant"]


def test_new_variant_requires_min_count():
    cards = _cards(["櫻花樂只出現一次", "印花樂是品牌"])
    glos = _glos("印花樂")
    cands = gc.suggest_candidates(cards, glos, dict_words=set())
    assert not [c for c in cands if c["kind"] == "new_variant"]  # 只 1 次 → 不報


def test_new_variant_skips_dictionary_words():
    cards = _cards(["印花布很好看", "印花布料", "印花樂是品牌"])
    glos = _glos("印花樂")
    cands = gc.suggest_candidates(cards, glos, dict_words={"印花布"})
    assert not [c for c in cands if c["word"] == "印花布"]  # 真詞 → 不當變體


# ---- 過濾 ----

def test_recurring_off_by_default():
    cards = _cards(["然後我們", "然後就是", "然後那個"] * 3)
    glos = _glos("印花樂")
    default = gc.suggest_candidates(cards, glos, dict_words=set())
    assert not [c for c in default if c["kind"] == "recurring_oov"]
    on = gc.suggest_candidates(cards, glos, dict_words=set(), include_recurring=True)
    assert isinstance(on, list)  # 開了不爆


def test_fillers_not_in_recurring():
    cards = _cards(["然後然後", "那個那個", "就是就是"] * 2)
    cands = gc.detect_recurring_oov(cards, _glos(), dict_words=set(), min_count=2)
    assert not [c for c in cands if c["word"] in ("然後", "那個", "就是")]


# ---- 輸出 ----

def test_yaml_block_schema_matches_glossary():
    cards = _cards(["就是櫻花樂", "櫻花樂網站"])
    glos = _glos("印花樂", "茄芷袋")
    cands = gc.suggest_candidates(cards, glos, dict_words=set())
    y = gc.to_yaml_block(cands)
    assert "glossary_candidates:" in y
    assert "canonical:" in y and "sounds_like:" in y
    assert "茄芷袋" in y                      # 高信心有列
    assert '["櫻花樂"]' in y                  # 變體寫進 sounds_like


def test_markdown_empty_is_graceful():
    md = gc.to_markdown([], "測試集", has_conf=False)
    assert "沒有偵測到" in md


# ---- 寫回 .glossary.json round-trip ----

def test_add_to_episode_glossary_roundtrip_and_union(tmp_path):
    gc.add_to_episode_glossary(tmp_path, "印花樂", sounds_like=["櫻花樂"])
    gc.add_to_episode_glossary(tmp_path, "印花樂", sounds_like=["印花業"])  # 同 canonical 再加
    data = json.loads((tmp_path / config.EPISODE_GLOSSARY_FILENAME).read_text(encoding="utf-8"))
    entry = [g for g in data if g["canonical"] == "印花樂"][0]
    assert set(entry["sounds_like"]) == {"櫻花樂", "印花業"}   # 聯集，不覆蓋


def test_low_prob_flushes_at_segment_end():
    # 低機率串延伸到該卡最後一字 → 必須被收（句尾正是 ASR 最常掉機率處）
    cards = _cards(["這是茄芷袋"])
    conf = {1: [{"w": "這", "p": 0.9}, {"w": "是", "p": 0.9},
                {"w": "茄", "p": 0.1}, {"w": "芷", "p": 0.1}, {"w": "袋", "p": 0.2}]}
    out = gc.detect_low_prob(cards, conf, thr=0.5)
    assert any("茄芷袋" in c["word"] for c in out)


def test_low_prob_empty_conf():
    assert gc.detect_low_prob(_cards(["x"]), {}) == []


def test_yaml_block_escapes_quotes_and_backslash():
    import yaml
    # glossary note 含雙引號與反斜線（episode.yaml 使用者自由輸入）
    glos = [{"canonical": '品牌"X"', "sounds_like": [], "note": 'path C:\\x 與 "引號"'}]
    cards = _cards(["今天沒提到這個"])      # → fed_but_missing
    cands = gc.suggest_candidates(cards, glos, dict_words=set())
    y = gc.to_yaml_block(cands)
    parsed = yaml.safe_load(y)              # 必須是合法 YAML
    assert "glossary_candidates" in parsed
    assert parsed["glossary_candidates"][0]["canonical"] == '品牌"X"'


def test_corrupt_glossary_backed_up_not_lost(tmp_path):
    from podcast_toolkit import config
    p = tmp_path / config.EPISODE_GLOSSARY_FILENAME
    p.write_text("{ this is not json ]]", encoding="utf-8")
    gc.add_to_episode_glossary(tmp_path, "茄芷袋", sounds_like=["茄子袋"])
    assert (tmp_path / (config.EPISODE_GLOSSARY_FILENAME + ".corrupt.bak")).exists()  # 壞檔有備份
    data = json.loads(p.read_text(encoding="utf-8"))
    assert any(g["canonical"] == "茄芷袋" for g in data)                              # 新檔合法


def test_ignore_filters_candidates(tmp_path, monkeypatch):
    # 假一個最小 episode：generate 走 Episode → 需要 episode.yaml + _final_v2.srt
    (tmp_path / "episode.yaml").write_text("name: t\ndate: 1\nglossary:\n  - canonical: 茄芷袋\n", encoding="utf-8")
    out = tmp_path / "03_成品"; out.mkdir()
    (out / "t_final_v2.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n今天天氣很好\n", encoding="utf-8")
    gc._save_ignore(tmp_path, {"茄芷袋"})
    cands = gc.generate(tmp_path, quiet=True)
    assert "茄芷袋" not in {c["word"] for c in cands}   # 被 ignore 濾掉
