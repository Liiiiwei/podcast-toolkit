"""FastAPI 五條路由的整合測試。"""
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app


@pytest.fixture
def client(tmp_episode_dir: Path):
    # 放一個假 main_video，讓 /api/video 有東西讀
    main_video = tmp_episode_dir / "01_母帶" / "測試集.mp4"
    main_video.write_bytes(b"FAKE" * 1000)

    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: None)
    return TestClient(app)


def test_get_root_serves_index_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


def test_get_episode_returns_state(client):
    r = client.get("/api/episode")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "測試集"
    assert isinstance(body["cards"], list)
    assert body["crop_yt"] is None
    assert body["crop_reels"] is None


def test_get_video_with_range_returns_206(client):
    r = client.get("/api/video", headers={"Range": "bytes=0-10"})
    assert r.status_code == 206
    assert "bytes 0-10/" in r.headers["content-range"]


def test_post_save_writes_files_and_signals_shutdown(client, tmp_episode_dir):
    called = {"n": 0}
    # 重建 client 但讓 shutdown 計次
    from podcast_toolkit.web.api import build_app
    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: called.__setitem__("n", called["n"] + 1))
    c = TestClient(app)
    r = c.post(
        "/api/save",
        json={"crop": None, "deletions": [3], "cards": []},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    import yaml
    data = yaml.safe_load((tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert data["deletions"] == [3]
    import time
    time.sleep(0.5)
    assert called["n"] == 1


def test_post_shutdown_calls_callback(client, tmp_episode_dir):
    called = {"n": 0}
    from podcast_toolkit.web.api import build_app
    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: called.__setitem__("n", called["n"] + 1))
    c = TestClient(app)
    r = c.post("/api/shutdown")
    assert r.status_code == 204
    import time
    time.sleep(0.5)
    assert called["n"] == 1


def test_pump_progress_renames_tmp_to_out_on_success(monkeypatch, tmp_path):
    """ffmpeg 結束碼 0 且 tmp_out 存在 → rename 到 out。"""
    from podcast_toolkit.web import assemble_job

    tmp_out = tmp_path / ".final.mp4.tmp"
    tmp_out.write_bytes(b"fake video bytes")
    out = tmp_path / "final.mp4"

    class FakeProc:
        stdout = iter(["progress=end\n"])
        stderr = type("S", (), {"read": lambda self: ""})()
        def wait(self): return 0

    assemble_job._pump_progress(FakeProc(), total_dur=10.0,
                                 out_path=out, tmp_out=tmp_out)
    assert out.exists()
    assert not tmp_out.exists()


def test_pump_progress_does_not_overwrite_on_failure(tmp_path):
    """ffmpeg 失敗 → tmp_out 砍掉、out 保持原狀。"""
    from podcast_toolkit.web import assemble_job

    out = tmp_path / "final.mp4"
    out.write_bytes(b"existing good output")
    tmp_out = tmp_path / ".final.mp4.tmp"
    tmp_out.write_bytes(b"half-baked output")

    class FakeProc:
        stdout = iter([])
        stderr = type("S", (), {"read": lambda self: "ffmpeg crashed"})()
        def wait(self): return 1

    assemble_job._pump_progress(FakeProc(), total_dur=10.0,
                                 out_path=out, tmp_out=tmp_out)
    assert out.read_bytes() == b"existing good output"
    assert not tmp_out.exists()  # tmp 要被清掉


def test_start_job_with_two_targets_queues_both(monkeypatch, tmp_episode_full):
    """start_job 接 ['yt', 'reels'] → 兩個都進 queue。"""
    from podcast_toolkit.web import assemble_job
    from podcast_toolkit.episode import Episode

    class _FakeProc:
        stdout = iter([])
        stderr = None
        def wait(self): return 0

    spawned = []
    monkeypatch.setattr(assemble_job, "Popen",
                        lambda *a, **k: spawned.append(a[0]) or _FakeProc())
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)

    ep = Episode(tmp_episode_full)
    # 確保 state 是 idle
    assemble_job._STATE["state"] = "idle"

    info = assemble_job.start_job(ep, targets=["yt", "reels"], force=True)

    state = assemble_job.get_status()
    assert state["queue"] == ["yt", "reels"]
    assert state["current"] == "yt"
    assert state["index"] == 0
    assert state["total"] == 2


def test_assemble_endpoint_requires_targets(client):
    r = client.post("/api/assemble", json={"force": True})
    assert r.status_code == 400
    assert "targets" in r.json()["detail"]


def test_assemble_endpoint_with_yt_reels(client, monkeypatch):
    from podcast_toolkit.web import assemble_job
    monkeypatch.setattr(assemble_job, "start_job",
                        lambda ep, targets, force: {
                            "targets": targets,
                            "out_paths": [f"/fake/{t}.mp4" for t in targets],
                        })
    r = client.post("/api/assemble",
                    json={"targets": ["yt", "reels"], "force": True})
    assert r.status_code == 200
    assert r.json()["targets"] == ["yt", "reels"]
    assert len(r.json()["out_paths"]) == 2


def test_start_job_rejects_when_running(tmp_episode_full):
    from podcast_toolkit.web import assemble_job
    from podcast_toolkit.episode import Episode

    assemble_job._STATE["state"] = "running"
    try:
        with pytest.raises(RuntimeError, match="已有"):
            assemble_job.start_job(Episode(tmp_episode_full),
                                   targets=["yt"], force=True)
    finally:
        assemble_job._STATE["state"] = "idle"
