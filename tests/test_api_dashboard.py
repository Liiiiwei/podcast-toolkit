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
    # dashboard.html 標題已改成「Podcast Toolkit」（與 index.html 共用），
    # 改用 dashboard.js script 作為唯一辨識 — index.html 載的是 app.js
    assert "dashboard.js" in r.text


def test_get_root_serves_edit_ui_when_ep(edit_client):
    r = edit_client.get("/")
    assert r.status_code == 200
    # index.html 標題
    assert "podcast edit" in r.text


def test_get_episodes_returns_list(dashboard_client, monkeypatch, tmp_path):
    """掛掉 CONFIG_PATH 與 episode_roots，確保 endpoint 串得通。"""
    from podcast_toolkit.web import api as api_mod
    fake_config = tmp_path / "config.json"
    fake_config.write_text('{"episode_roots": []}', encoding="utf-8")
    monkeypatch.setattr(api_mod, "CONFIG_PATH", fake_config)

    r = dashboard_client.get("/api/episodes")
    assert r.status_code == 200
    body = r.json()
    assert "episodes" in body
    assert "warnings" in body
    assert isinstance(body["episodes"], list)


def test_post_open_switches_to_edit_mode(dashboard_client, tmp_episode_dir, monkeypatch, tmp_path):
    """open 後同一 client 的 GET / 應回 edit UI。"""
    from podcast_toolkit.web import api as api_mod
    fake_config = tmp_path / "config.json"
    monkeypatch.setattr(api_mod, "CONFIG_PATH", fake_config)
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"X")

    r = dashboard_client.post("/api/episodes/open", json={"path": str(tmp_episode_dir)})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r2 = dashboard_client.get("/")
    assert "podcast edit" in r2.text

    # recent 已寫入
    import json
    recent = json.loads(fake_config.read_text(encoding="utf-8"))["recent_episodes"]
    assert recent == [str(tmp_episode_dir)]


def test_post_open_400_for_missing_path(dashboard_client, tmp_path):
    r = dashboard_client.post("/api/episodes/open", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 400


def test_post_open_400_for_no_episode_yaml(dashboard_client, tmp_path):
    folder = tmp_path / "not_episode"
    folder.mkdir()
    r = dashboard_client.post("/api/episodes/open", json={"path": str(folder)})
    assert r.status_code == 400


def test_post_close_clears_ep(edit_client):
    r = edit_client.post("/api/episodes/close")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # 之後 GET / 應該回 dashboard
    r2 = edit_client.get("/")
    assert "Dashboard" in r2.text


def test_config_includes_episode_roots(dashboard_client, monkeypatch, tmp_path):
    from podcast_toolkit.web import api as api_mod
    fake_config = tmp_path / "config.json"
    monkeypatch.setattr(api_mod, "CONFIG_PATH", fake_config)

    r = dashboard_client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "episode_roots" in body
    assert isinstance(body["episode_roots"], list)


def test_post_config_saves_episode_roots(dashboard_client, monkeypatch, tmp_path):
    from podcast_toolkit.web import api as api_mod
    fake_config = tmp_path / "config.json"
    monkeypatch.setattr(api_mod, "CONFIG_PATH", fake_config)

    r = dashboard_client.post("/api/config", json={"episode_roots": ["~/Podcasts", "~/Downloads"]})
    assert r.status_code == 200
    assert r.json()["episode_roots"] == ["~/Podcasts", "~/Downloads"]

    import json
    saved = json.loads(fake_config.read_text(encoding="utf-8"))
    assert saved["episode_roots"] == ["~/Podcasts", "~/Downloads"]


def test_post_config_rejects_non_list_episode_roots(dashboard_client):
    r = dashboard_client.post("/api/config", json={"episode_roots": "not a list"})
    assert r.status_code == 400
