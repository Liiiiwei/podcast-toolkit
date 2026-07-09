"""上架文案產生器（publish_doc.py）測試。

不實際呼叫 claude——用 monkeypatch 假裝模型回覆，專注驗證確定性層：
時間戳格式、逐字稿壓縮、章節清洗（強制 0:00／單調／間隔／去超片長）、
JSON 容錯解析、文件渲染的分區與名詞對照。
"""
from __future__ import annotations

import pytest

from podcast_toolkit import publish_doc as pd


@pytest.mark.parametrize("sec,expected", [
    (0, "0:00"),
    (44, "0:44"),
    (2521, "42:01"),
    (3600, "1:00:00"),
    (3725, "1:02:05"),
])
def test_fmt_ts(sec, expected):
    assert pd._fmt_ts(sec) == expected


@pytest.mark.parametrize("ts,expected", [
    ("0:44", 44),
    ("1:02:05", 3725),
    ("bad", -1),
    ("", -1),
])
def test_ts_to_sec(ts, expected):
    assert pd._ts_to_sec(ts) == expected


def test_transcript_for_prompt_uses_final_timeline():
    srt = "1\n00:00:44,000 --> 00:00:46,000\n留白計畫在做什麼\n\n"
    out = pd._transcript_for_prompt(srt)
    assert out == "0:44 留白計畫在做什麼"


def test_normalize_chapters_forces_zero_monotonic_gap_and_bounds():
    raw = [
        {"time": "0:05", "title": "開場"},   # 首個 → 強制 0:00
        {"time": "0:10", "title": "B"},      # 距 0 有 10s → 留
        {"time": "0:15", "title": "太近丟"},  # 距 B 只 5s → 丟
        {"time": "1:00", "title": "C"},
        {"time": "99:00", "title": "超片長丟"},
        {"time": "bad", "title": "壞格式丟"},
        {"time": "2:00", "title": ""},        # 空標題丟
    ]
    ch = pd._normalize_chapters(raw, total_dur=2521)
    assert [c["sec"] for c in ch] == [0, 10, 60]
    assert ch[0]["title"] == "開場"


def test_normalize_chapters_empty_returns_empty():
    assert pd._normalize_chapters([], total_dur=100) == []


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('前言廢話 {"b": 2} 後綴', {"b": 2}),
    ('{"nested": {"x": 1}} 尾巴', {"nested": {"x": 1}}),
])
def test_extract_json_object(text, expected):
    assert pd._extract_json_object(text) == expected


def test_extract_json_object_no_json_raises():
    with pytest.raises(pd.PublishDocError):
        pd._extract_json_object("完全沒有大括號")


def test_render_doc_has_all_sections_and_glossary():
    data = {
        "title_recommended": "測試標題｜我愛上班 EP__",
        "title_alternatives": ["備選一", "備選二"],
        "description": "鉤子。\n介紹來賓。",
        "highlights": ["重點一", "重點二"],
        "social": "社群短文",
        "hashtags": ["我愛上班", "#測試"],
    }
    ctx = {"show": "我愛上班", "hosts": ["郝慧川", "岳啟儒"], "guest": "魁哥",
           "links": ["報名連結"]}
    meta = {
        "name": "魁哥",
        "duration_str": "42:01",
        "chapters": [{"sec": 0, "title": "開場"}, {"sec": 44, "title": "在做什麼"}],
        "glossary_terms": ["魁哥（來賓藝名）", "播籃球"],
    }
    out = pd.render_doc(data, ctx, meta)
    assert "一、影片標題" in out
    assert "★ 推薦" in out and "測試標題" in out
    assert "二、YouTube 說明欄" in out
    assert "0:00 開場" in out            # 章節（說明欄內）
    assert "0:44\t在做什麼" in out        # 章節（單獨版・tab 分隔）
    assert "四、社群短文" in out
    assert "五、待補欄位" in out
    assert "☐ 連結：報名連結" in out
    assert "重要名詞對照" in out
    assert "魁哥（來賓藝名）、播籃球" in out
    # hashtag 去重前綴後統一單一 #
    assert "#我愛上班" in out and "#測試" in out


def test_render_doc_missing_hosts_adds_todo():
    data = {"title_recommended": "T", "title_alternatives": [], "description": "d",
            "highlights": [], "social": "", "hashtags": []}
    ctx = {"show": "", "hosts": [], "guest": "", "links": []}
    meta = {"name": "ep", "duration_str": "1:00", "chapters": [{"sec": 0, "title": "開"}],
            "glossary_terms": []}
    out = pd.render_doc(data, ctx, meta)
    assert "☐ 主持人掛名" in out
    assert "☐ 相關連結" in out           # 無 links → 通用待補提示
