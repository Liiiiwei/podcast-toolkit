"""字幕語意校對引擎(proofread.py)測試。

不實際呼叫 claude / gemini——provider 用 monkeypatch 假裝,專注驗證:
prompt 組裝、JSON 容錯解析、安全閘(QA)、provider 解析、套用 + 備份。
"""
from __future__ import annotations

import pytest

from podcast_toolkit import proofread, srt_io
from podcast_toolkit.episode import Episode


def test_build_prompt_has_rules_glossary_cards_and_json_instruction():
    cards = [{"idx": 1, "start": 0.0, "end": 1.0, "text": "他是放牛班的班長"}]
    glossary = [{"canonical": "郝慧川", "sounds_like": ["好慧川"], "note": "主持人"}]
    p = proofread.build_prompt(cards, glossary, context="酪農訪談")
    assert "校對規則" in p
    assert "郝慧川" in p and "好慧川" in p          # 詞庫有渲染進去
    assert "1\t他是放牛班的班長" in p                # 卡片以 idx<TAB>原文 帶入
    assert "酪農訪談" in p                           # context 有帶
    assert "只輸出一個 JSON" in p                    # 嚴格輸出指示


@pytest.mark.parametrize("raw,expected", [
    ('{"3":"修好"}', {"3": "修好"}),
    ('```json\n{"3":"修好"}\n```', {"3": "修好"}),
    ('好的,結果如下:\n{"3":"修好","7":"另一句"}\n以上。', {"3": "修好", "7": "另一句"}),
])
def test_extract_json_object_tolerates_noise(raw, expected):
    assert proofread._extract_json_object(raw) == expected


def test_extract_json_object_raises_on_garbage():
    with pytest.raises(proofread.ProofreadError):
        proofread._extract_json_object("這完全不是 JSON,也沒有大括號")


def test_qa_filter_keeps_legit_reverts_fabrication():
    by = {
        1: {"idx": 1, "text": "他是放牛班的班長"},   # 同音/加空格小修 → 保留
        2: {"idx": 2, "text": "哎"},                  # 短卡被換成長句 = 捏造 → 還原
        3: {"idx": 3, "text": "對啊"},                # 去填充詞 → 保留
    }
    corrections = {
        1: "他是放牛班的班長 雙關",
        2: "我們兩點其實看每個人的工作分配",          # sim≈0 且淨增很多
        3: "對",
    }
    applied, reverted = proofread.qa_filter(by, corrections)
    assert applied == {1: "他是放牛班的班長 雙關", 3: "對"}
    assert [r[0] for r in reverted] == [2]


def test_qa_filter_skips_noop_and_empty():
    by = {1: {"idx": 1, "text": "原文"}, 2: {"idx": 2, "text": "保留"}}
    applied, reverted = proofread.qa_filter(by, {1: "原文", 2: "  "})
    assert applied == {} and reverted == []          # 沒變的 / 空字串都不算修改


def test_resolve_provider_explicit_and_auto(monkeypatch):
    assert proofread.resolve_provider({"proofread": {"provider": "off"}}) is None
    assert proofread.resolve_provider({"proofread": {"provider": "gemini"}}) == "gemini"

    # auto + 有 claude CLI → claude_code
    monkeypatch.setattr(proofread.shutil, "which",
                        lambda n: "/x/claude" if n == "claude" else None)
    assert proofread.resolve_provider({"proofread": {"provider": "auto"}}) == "claude_code"

    # auto + 無 claude + 有 gemini key → gemini
    monkeypatch.setattr(proofread.shutil, "which", lambda n: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert proofread.resolve_provider(
        {"proofread": {"provider": "auto"}, "gemini_api_key": "k"}) == "gemini"

    # auto + 什麼引擎都沒有 → None(非 CC 使用者且沒 key:安靜跳過)
    assert proofread.resolve_provider({"proofread": {"provider": "auto"}}) is None


def test_proofread_cards_dispatches_and_normalizes_keys(monkeypatch):
    cards = [{"idx": 1, "text": "他是放牛班的班長"}, {"idx": 2, "text": "對啊"}]

    def fake(cards_, glossary, *, cfg, progress=None):
        return {"2": "對"}      # 故意回 str key,驗證正規化成 int

    monkeypatch.setitem(proofread.PROVIDERS, "gemini", fake)
    prov, applied, reverted = proofread.proofread_cards(
        cards, [], {"proofread": {"provider": "gemini"}})
    assert prov == "gemini"
    assert applied == {2: "對"}
    assert reverted == []


def test_proofread_cards_off_returns_none(monkeypatch):
    monkeypatch.setattr(proofread.shutil, "which", lambda n: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    prov, applied, reverted = proofread.proofread_cards(
        [{"idx": 1, "text": "x"}], [], {"proofread": {"provider": "auto"}})
    assert prov is None and applied == {} and reverted == []


def test_run_applies_corrections_and_backs_up(tmp_episode_dir, monkeypatch):
    ep = Episode(tmp_episode_dir)
    v2 = ep.output_v2_srt()
    cards = srt_io.parse(v2.read_text(encoding="utf-8"))
    first_idx = cards[0]["idx"]
    new_text = cards[0]["text"] + " 校對過"

    def fake(cards_, glossary, *, cfg, progress=None):
        return {first_idx: new_text}

    monkeypatch.setitem(proofread.PROVIDERS, "claude_code", fake)
    rc = proofread.run(tmp_episode_dir, provider="claude_code")
    assert rc == 0

    after = srt_io.parse(v2.read_text(encoding="utf-8"))
    assert after[0]["text"] == new_text                       # 第一卡已套用
    assert len(after) == len(cards)                           # 卡數不變

    backup = v2.with_name(f"{v2.stem}.pre-proofread.bak{v2.suffix}")
    assert backup.exists()
    assert srt_io.parse(backup.read_text(encoding="utf-8"))[0]["text"] != new_text  # 備份是原文


def test_run_off_skips_without_touching_file(tmp_episode_dir):
    v2 = Episode(tmp_episode_dir).output_v2_srt()
    before = v2.read_text(encoding="utf-8")
    rc = proofread.run(tmp_episode_dir, provider="off")
    assert rc == 0
    assert v2.read_text(encoding="utf-8") == before           # off:檔案沒被動
    backup = v2.with_name(f"{v2.stem}.pre-proofread.bak{v2.suffix}")
    assert not backup.exists()


def test_run_missing_srt_returns_3(tmp_path):
    ep = tmp_path / "20260601 空集"
    (ep / "03_成品").mkdir(parents=True)
    import yaml
    (ep / "episode.yaml").write_text(
        yaml.safe_dump({"date": 20260601, "name": "空集"}, allow_unicode=True),
        encoding="utf-8")
    assert proofread.run(ep, provider="claude_code") == 3     # 沒 _v2.srt → exit 3


def test_run_skips_deleted_cards(tmp_episode_dir, monkeypatch):
    import yaml
    yp = tmp_episode_dir / "episode.yaml"
    d = yaml.safe_load(yp.read_text(encoding="utf-8"))
    d["deletions"] = [2]                                      # 第 2 卡標刪除
    yp.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")

    seen: list[int] = []

    def fake(cards_, glossary, *, cfg, progress=None):
        seen.extend(c["idx"] for c in cards_)
        return {}

    monkeypatch.setitem(proofread.PROVIDERS, "claude_code", fake)
    proofread.run(tmp_episode_dir, provider="claude_code")
    assert 2 not in seen                                      # 已刪卡不送校對
    assert {1, 3, 4} <= set(seen)                             # 其餘照常


def test_run_claude_code_resilient_to_partial_failure(monkeypatch):
    cards = [{"idx": i, "text": f"卡{i}"} for i in range(1, 7)]   # 6 卡 / chunk 2 → 3 塊

    def fake_chunk(chunk, glossary, *, model, timeout, context):
        if chunk[0]["idx"] == 1:                              # 第一塊故意失敗
            raise proofread.ProofreadError("假裝逾時")
        return {str(chunk[0]["idx"]): "修好"}

    monkeypatch.setattr(proofread, "_claude_one_chunk", fake_chunk)
    monkeypatch.setattr(proofread.shutil, "which", lambda n: "/x/claude")
    out = proofread._run_claude_code(
        cards, [], cfg={"proofread": {"chunk_size": 2, "max_workers": 3}})
    assert out == {3: "修好", 5: "修好"}                       # 失敗塊跳過,其餘照樣回


def test_run_claude_code_all_fail_raises(monkeypatch):
    def fake_chunk(*a, **k):
        raise proofread.ProofreadError("全掛")

    monkeypatch.setattr(proofread, "_claude_one_chunk", fake_chunk)
    monkeypatch.setattr(proofread.shutil, "which", lambda n: "/x/claude")
    with pytest.raises(proofread.ProofreadError):
        proofread._run_claude_code(
            [{"idx": 1, "text": "a"}], [], cfg={"proofread": {"chunk_size": 1}})
