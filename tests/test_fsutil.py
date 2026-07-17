"""fsutil.atomic_write_text 與 config.json 壞檔隔離的測試。"""
import json

import pytest

from podcast_toolkit import fsutil
from podcast_toolkit.fsutil import atomic_write_text


def test_atomic_write_creates_file_and_parents(tmp_path):
    target = tmp_path / "深層" / "資料夾" / "episode.yaml"
    atomic_write_text(target, "name: 測試集\n")
    assert target.read_text(encoding="utf-8") == "name: 測試集\n"


def test_atomic_write_overwrites_existing(tmp_path):
    target = tmp_path / "config.json"
    target.write_text("舊內容", encoding="utf-8")
    atomic_write_text(target, "新內容")
    assert target.read_text(encoding="utf-8") == "新內容"


def test_atomic_write_leaves_no_tmp_residue(tmp_path):
    target = tmp_path / "a.srt"
    atomic_write_text(target, "1\n00:00:00,000 --> 00:00:01,000\n哈囉\n")
    # 成功寫入後同目錄不能殘留 tmp 檔
    assert [p.name for p in tmp_path.iterdir()] == ["a.srt"]


def test_interrupted_write_keeps_original_and_cleans_tmp(tmp_path, monkeypatch):
    """寫入中斷模擬：寫到一半（fsync 時）丟例外 → 原檔完好、無半寫殘留。"""
    target = tmp_path / "episode.yaml"
    target.write_text("date: 20260601\nname: 原始集\n", encoding="utf-8")

    def _boom(_fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fsutil.os, "fsync", _boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "date: 20260601\nname: 寫到一半就斷電的新內容\n")

    # 原檔內容一個字都不能少
    assert target.read_text(encoding="utf-8") == "date: 20260601\nname: 原始集\n"
    # 同目錄不能留下 .tmp* 半成品
    assert [p.name for p in tmp_path.iterdir()] == ["episode.yaml"]


def test_interrupted_write_on_new_file_leaves_nothing(tmp_path, monkeypatch):
    """對「尚不存在的檔」寫入中斷 → 目錄乾淨，不會出現截斷的新檔。"""
    target = tmp_path / "new.srt"

    def _boom(_fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fsutil.os, "fsync", _boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "半寫內容")

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


# ── config.json 壞檔隔離（api._load_config）─────────────────────────────


def test_load_config_quarantines_corrupt_json(tmp_path, monkeypatch):
    """壞 JSON → 搬成 config.json.corrupt-{ts} 保留現場，回空 dict。"""
    from podcast_toolkit.web import api

    cfg = tmp_path / "config.json"
    cfg.write_text('{"gemini_api_key": "abc", "epis', encoding="utf-8")  # 截斷的壞檔
    monkeypatch.setattr(api, "CONFIG_PATH", cfg)

    assert api._load_config() == {}
    # 原路徑已被搬走，壞內容保留在 corrupt 檔裡可手救
    assert not cfg.exists()
    corrupt = list(tmp_path.glob("config.json.corrupt-*"))
    assert len(corrupt) == 1
    assert corrupt[0].read_text(encoding="utf-8") == '{"gemini_api_key": "abc", "epis'


def test_load_config_normal_roundtrip(tmp_path, monkeypatch):
    """好檔正常讀；save → load 走原子寫入來回一致。"""
    from podcast_toolkit.web import api

    cfg = tmp_path / "config.json"
    monkeypatch.setattr(api, "CONFIG_PATH", cfg)

    api._save_config({"gemini_api_key": "abc", "episode_roots": ["/tmp/eps"]})
    assert api._load_config() == {
        "gemini_api_key": "abc",
        "episode_roots": ["/tmp/eps"],
    }
    # 沒有殘留 tmp / corrupt 檔
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_load_config_missing_file_returns_empty(tmp_path, monkeypatch):
    from podcast_toolkit.web import api

    monkeypatch.setattr(api, "CONFIG_PATH", tmp_path / "no-such.json")
    assert api._load_config() == {}


def test_save_typo_dict_atomic_roundtrip(tmp_path, monkeypatch):
    from podcast_toolkit.web import api

    p = tmp_path / "typo-dict.json"
    monkeypatch.setattr(api, "TYPO_DICT_PATH", p)
    api._save_typo_dict([{"wrong": "過嗨", "right": "乳牛", "note": ""}])
    assert json.loads(p.read_text(encoding="utf-8"))[0]["wrong"] == "過嗨"
    assert api._load_typo_dict()[0]["right"] == "乳牛"
