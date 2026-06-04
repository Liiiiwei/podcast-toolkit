"""T23c：手動標記三檔聲音事件 → 算 offset。

設計：
- audio_align.compute_manual_offset(events) → (offset_sec, deltas)
  events: [{"a": float, "b": float}, ...]
  offset = mean(a[i] - b[i])
  deltas[i] = (a[i] - b[i]) - offset  → 看三筆的一致性
- POST /api/manual-align {"events": [...]}
  回 {"ok": True, "offset_sec": float, "deltas": [...]}
  少於 / 多於 3 筆 → 400
  任一 a/b 不是數字 → 400
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from podcast_toolkit import audio_align
from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app


# ---------- compute_manual_offset helper ----------

def test_compute_manual_offset_three_consistent_events():
    """三筆完美一致 → offset 等於差值，deltas 全 0。"""
    events = [
        {"a": 1.50, "b": 1.00},
        {"a": 5.80, "b": 5.30},
        {"a": 12.0, "b": 11.5},
    ]
    offset, deltas = audio_align.compute_manual_offset(events)
    assert offset == pytest.approx(0.5)
    assert deltas == pytest.approx([0.0, 0.0, 0.0])


def test_compute_manual_offset_with_noise_returns_mean():
    """有雜訊時 offset = 平均，deltas 反映每筆偏離平均的量。"""
    events = [
        {"a": 1.0, "b": 0.5},   # delta = 0.5
        {"a": 5.0, "b": 4.4},   # delta = 0.6
        {"a": 10.0, "b": 9.6},  # delta = 0.4
    ]
    offset, deltas = audio_align.compute_manual_offset(events)
    assert offset == pytest.approx(0.5)
    assert deltas == pytest.approx([0.0, 0.1, -0.1])


def test_compute_manual_offset_rejects_wrong_event_count():
    with pytest.raises(ValueError):
        audio_align.compute_manual_offset([{"a": 1.0, "b": 0.5}])  # 1
    with pytest.raises(ValueError):
        audio_align.compute_manual_offset([])  # 0
    with pytest.raises(ValueError):
        # 4 筆也拒（規格鎖三筆，避免使用者瞎標）
        audio_align.compute_manual_offset([
            {"a": 1.0, "b": 0.5},
            {"a": 2.0, "b": 1.5},
            {"a": 3.0, "b": 2.5},
            {"a": 4.0, "b": 3.5},
        ])


def test_compute_manual_offset_rejects_non_numeric():
    with pytest.raises(ValueError):
        audio_align.compute_manual_offset([
            {"a": 1.0, "b": 0.5},
            {"a": "abc", "b": 1.5},  # a 不是數字
            {"a": 3.0, "b": 2.5},
        ])
    with pytest.raises(ValueError):
        audio_align.compute_manual_offset([
            {"a": 1.0, "b": None},
            {"a": 2.0, "b": 1.5},
            {"a": 3.0, "b": 2.5},
        ])


# ---------- POST /api/manual-align ----------

@pytest.fixture
def client(tmp_episode_dir):
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"FAKE")
    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: None)
    return TestClient(app)


def test_post_manual_align_returns_offset_and_deltas(client):
    r = client.post(
        "/api/manual-align",
        json={
            "events": [
                {"a": 1.0, "b": 0.5},
                {"a": 5.0, "b": 4.4},
                {"a": 10.0, "b": 9.6},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["offset_sec"] == pytest.approx(0.5)
    assert body["deltas"] == pytest.approx([0.0, 0.1, -0.1])


def test_post_manual_align_rejects_wrong_event_count(client):
    r = client.post("/api/manual-align", json={"events": [{"a": 1.0, "b": 0.5}]})
    assert r.status_code == 400
    assert "三" in r.text or "3" in r.text


def test_post_manual_align_rejects_missing_events(client):
    r = client.post("/api/manual-align", json={})
    assert r.status_code == 400


def test_post_manual_align_rejects_non_numeric(client):
    r = client.post(
        "/api/manual-align",
        json={
            "events": [
                {"a": 1.0, "b": 0.5},
                {"a": "bad", "b": 1.5},
                {"a": 3.0, "b": 2.5},
            ]
        },
    )
    assert r.status_code == 400
