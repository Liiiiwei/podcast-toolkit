"""影片 Range streaming：支援 HTML5 <video> 分段抓檔。"""
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from podcast_toolkit.web import video as video_mod


@pytest.fixture
def fake_video(tmp_path: Path) -> Path:
    p = tmp_path / "v.mp4"
    p.write_bytes(b"X" * 10000)
    return p


@pytest.fixture
def client(fake_video):
    # 建一個只暴露 /video 的小 app，讓 TestClient 驅動 ASGI
    async def endpoint(request):
        return video_mod.range_response(
            fake_video,
            range_header=request.headers.get("range"),
        )

    app = Starlette(routes=[Route("/video", endpoint)])
    from starlette.testclient import TestClient
    return TestClient(app)


def test_range_response_returns_206_with_correct_slice(client):
    resp = client.get("/video", headers={"Range": "bytes=0-99"})
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 0-99/10000"
    assert resp.headers["content-length"] == "100"
    assert len(resp.content) == 100


def test_range_response_open_ended(client):
    resp = client.get("/video", headers={"Range": "bytes=9990-"})
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 9990-9999/10000"
    assert resp.headers["content-length"] == "10"


def test_no_range_returns_full_200(client):
    resp = client.get("/video")
    assert resp.status_code == 200
    assert resp.headers["content-length"] == "10000"


def test_range_out_of_bounds_returns_416(client):
    resp = client.get("/video", headers={"Range": "bytes=99999-"})
    assert resp.status_code == 416
