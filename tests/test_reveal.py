"""POST /api/reveal：用 macOS `open` 開檔/資料夾。

- 路徑必須在 ep.dir 內（避免被當任意檔案開啟器）
- 檔案 → open -R（Finder 選中）；資料夾 → open
- 缺 path → 400；越界 → 400；不存在 → 404
- 本地測試把 subprocess.run monkeypatch 掉，不真的開 Finder
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from podcast_toolkit.episode import Episode
from podcast_toolkit.web import api as api_mod
from podcast_toolkit.web.api import build_app


@pytest.fixture
def client_and_dir(tmp_episode_dir: Path, monkeypatch):
    # 假 main_video 滿足 Episode 期待
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"FAKE")
    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: None)

    # 攔 subprocess.run 不真的呼 macOS open
    calls: list[list[str]] = []

    def fake_run(cmd, check=False, **_):
        calls.append(list(cmd))
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(api_mod.subprocess, "run", fake_run)
    return TestClient(app), tmp_episode_dir, calls


def test_reveal_file_uses_open_dash_r(client_and_dir):
    """有檔案 → 呼 `open -R <abs>`，Finder 會選中。"""
    client, ep_dir, calls = client_and_dir
    target = ep_dir / "03_成品" / "final.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"FAKE")

    r = client.post("/api/reveal", json={"path": "03_成品/final.mp4"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert calls == [["open", "-R", str(target.resolve())]]


def test_reveal_folder_uses_plain_open(client_and_dir):
    """資料夾 → 不用 -R，直接 open。"""
    client, ep_dir, calls = client_and_dir
    folder = ep_dir / "03_成品"
    folder.mkdir(parents=True, exist_ok=True)

    r = client.post("/api/reveal", json={"path": "03_成品"})
    assert r.status_code == 200
    assert calls == [["open", str(folder.resolve())]]


def test_reveal_rejects_missing_path(client_and_dir):
    client, _, calls = client_and_dir
    r = client.post("/api/reveal", json={})
    assert r.status_code == 400
    assert calls == []


def test_reveal_rejects_escape_via_relative_path(client_and_dir):
    """限制在 episode dir 內 — ../ 越界要 400。"""
    client, _, calls = client_and_dir
    r = client.post("/api/reveal", json={"path": "../etc/passwd"})
    assert r.status_code == 400
    assert calls == []


def test_reveal_rejects_absolute_path_outside_episode(client_and_dir):
    client, _, calls = client_and_dir
    r = client.post("/api/reveal", json={"path": "/etc/passwd"})
    assert r.status_code == 400
    assert calls == []


def test_reveal_returns_404_for_nonexistent(client_and_dir):
    client, _, calls = client_and_dir
    r = client.post("/api/reveal", json={"path": "03_成品/nope.mp4"})
    assert r.status_code == 404
    assert calls == []
