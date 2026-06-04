"""Dashboard 模式 API 測試（ep=None）。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app


@pytest.fixture
def dashboard_client():
    app = build_app(ep=None, shutdown=lambda: None)
    return TestClient(app)


@pytest.fixture
def edit_client(tmp_episode_dir: Path):
    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE" * 1000)
    ep = Episode(tmp_episode_dir)
    app = build_app(ep=ep, shutdown=lambda: None)
    return TestClient(app)


def test_get_root_serves_dashboard_when_no_ep(dashboard_client):
    r = dashboard_client.get("/")
    assert r.status_code == 200
    assert "Dashboard" in r.text


def test_get_root_serves_edit_ui_when_ep(edit_client):
    r = edit_client.get("/")
    assert r.status_code == 200
    # index.html 標題
    assert "podcast edit" in r.text
