"""靜態資源快取策略測試：/static 回 no-cache＋ETag 304，HTML 回 no-store。

背景：以前靠 index.html/dashboard.html 手動 ?v= 撞號控制快取，實務上常漏撞
（改了 app.js 沒撞號 → 使用者跑舊版 JS）。根治法是後端對 /static 一律回
Cache-Control: no-cache（api.py NoCacheStaticFiles），瀏覽器每次 revalidate、
檔案沒變就 304。這組測試釘住該行為，避免之後改回裸 StaticFiles。
"""
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


def test_static_responds_no_cache(dashboard_client):
    """/static 資源必須帶 Cache-Control: no-cache（每次 revalidate）。"""
    r = dashboard_client.get("/static/app.css")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"
    # StaticFiles 內建條件請求所需的 validator
    assert "etag" in r.headers


def test_static_conditional_request_returns_304(dashboard_client):
    """帶 If-None-Match 重打，檔案沒變要回 304，且 304 也帶 no-cache。"""
    first = dashboard_client.get("/static/app.js")
    assert first.status_code == 200
    etag = first.headers["etag"]

    second = dashboard_client.get(
        "/static/app.js", headers={"If-None-Match": etag}
    )
    assert second.status_code == 304
    assert second.headers.get("cache-control") == "no-cache"


@pytest.mark.parametrize(
    "asset",
    ["/static/tokens.css", "/static/icons.js", "/static/dashboard.js"],
)
def test_all_static_assets_no_cache(dashboard_client, asset):
    """兩頁共用的子資源逐一確認都吃到 no-cache（不再依賴 ?v= 撞號）。"""
    r = dashboard_client.get(asset)
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"


def test_dashboard_html_not_cacheable(dashboard_client):
    """dashboard.html（ep=None 時的 "/"）不可被瀏覽器長快取。"""
    r = dashboard_client.get("/")
    assert r.status_code == 200
    assert "dashboard.js" in r.text
    assert r.headers.get("cache-control") == "no-store"


def test_index_html_not_cacheable(edit_client):
    """index.html（有 ep 時的 "/"）不可被瀏覽器長快取。"""
    r = edit_client.get("/")
    assert r.status_code == 200
    assert "app.js" in r.text
    assert r.headers.get("cache-control") == "no-store"


@pytest.mark.parametrize("page", ["index.html", "dashboard.html"])
def test_html_has_no_manual_version_params(page):
    """HTML 裡不得殘留手動 ?v= 撞號（快取控制已移到後端 header）。"""
    static_dir = Path(__file__).resolve().parents[1] / (
        "podcast_toolkit/web/static"
    )
    text = (static_dir / page).read_text(encoding="utf-8")
    assert "?v=" not in text
