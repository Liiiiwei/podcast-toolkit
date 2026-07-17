"""P0-2 零雲端金鑰一致化：前後端預設 provider 統一為本地 breeze。

鎖住兩件事：
1. 後端 /api/config 與 /api/transcribe 的預設 provider 都吃
   routes/config.py 的 DEFAULT_STT_PROVIDER（單一事實來源），空 config
   從 dashboard 走到轉錄不會撞「尚未設定 API key」死路。
2. 前端（index.html / dashboard.html / dashboard.js / app.js）的使用者
   可見文案不再殘留 Gemini／OpenAI／API key 雲端時代字樣；無 key modal
   的「去設定」死路鈕與雲端進度 pills 已移除。
   （gemini 後端碼路刻意保留：進階使用者手改 config 選 gemini 仍可用。）
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import podcast_toolkit.web as web_pkg
from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app
from podcast_toolkit.web.routes.config import DEFAULT_STT_PROVIDER

STATIC = Path(web_pkg.__file__).parent / "static"
INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")
DASHBOARD_HTML = (STATIC / "dashboard.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
DASHBOARD_JS = (STATIC / "dashboard.js").read_text(encoding="utf-8")


# ---------- 後端：預設 provider 一致為 breeze ----------

def test_default_stt_provider_constant_is_breeze():
    # 單一事實來源：兩條路由共用這個常數
    assert DEFAULT_STT_PROVIDER == "breeze"


@pytest.fixture
def app_client(tmp_episode_dir):
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"FAKE")
    ep = Episode(tmp_episode_dir)
    app = build_app(ep, shutdown=lambda: None)
    return TestClient(app)


def test_fresh_user_config_defaults_to_breeze(monkeypatch, app_client):
    """全新使用者（空 config、無任何 key）→ provider = breeze + breeze 狀態欄。"""
    from podcast_toolkit.web import api as api_mod
    monkeypatch.setattr(api_mod, "_load_config", lambda: {})
    body = app_client.get("/api/config").json()
    assert body["provider"] == "breeze"
    # dashboard 狀態 pill 靠這個欄位；available 依本機有沒有裝 Breeze 而異，只驗形狀
    assert isinstance(body.get("breeze"), dict)
    assert set(body["breeze"]) >= {"available", "dir"}


def test_post_config_accepts_breeze_provider(monkeypatch, app_client):
    saved = {}
    from podcast_toolkit.web import api as api_mod
    monkeypatch.setattr(api_mod, "_load_config", lambda: {})
    monkeypatch.setattr(api_mod, "_save_config", lambda d: saved.update(d))
    r = app_client.post("/api/config", json={"provider": "breeze"})
    assert r.status_code == 200
    assert saved.get("transcribe", {}).get("provider") == "breeze"
    assert r.json()["provider"] == "breeze"


def test_fresh_user_transcribe_guides_to_breeze_not_api_key(
    monkeypatch, tmp_episode_dir
):
    """空 config 打 /api/transcribe：不再回「尚未設定 API key」死路，
    改回 400 引導走一鍵 Breeze。"""
    from podcast_toolkit.web import api as api_mod
    from podcast_toolkit.web import transcribe_job as job_mod
    job_mod._reset()

    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"FAKE")
    monkeypatch.setattr(api_mod, "_load_config", lambda: {})

    ep = Episode(tmp_episode_dir)
    app = api_mod.build_app(ep, shutdown=lambda: None)
    c = TestClient(app)
    r = c.post("/api/transcribe", json={"path": "01_母帶/測試集.mp4"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "Breeze" in detail
    assert "API key" not in detail
    assert "Gemini" not in detail


# ---------- 前端靜態掃描：使用者可見文案不含雲端字樣 ----------

_CLOUD_WORDS = re.compile(r"gemini|openai|api[ -]?key", re.IGNORECASE)


def test_dashboard_has_no_cloud_wording():
    # dashboard 兩檔已無 gemini 碼路，可全檔掃
    for name, text in (("dashboard.html", DASHBOARD_HTML), ("dashboard.js", DASHBOARD_JS)):
        hits = _CLOUD_WORDS.findall(text)
        assert not hits, f"{name} 殘留雲端字樣：{hits}"


def test_index_html_has_no_cloud_wording():
    hits = _CLOUD_WORDS.findall(INDEX_HTML)
    assert not hits, f"index.html 殘留雲端字樣：{hits}"


def test_dashboard_renders_breeze_status_pill():
    assert 'id="config-status-stt"' in DASHBOARD_HTML, "Breeze 狀態 pill 容器被刪了"
    assert "config-status-keys" not in DASHBOARD_HTML
    assert "config-status-stt" in DASHBOARD_JS
    assert "cfg.breeze" in DASHBOARD_JS


def test_app_js_default_provider_is_breeze():
    assert 'sttProvider: "breeze"' in APP_JS


def test_app_js_no_key_modal_dead_end_removed():
    """無 key modal 不再指向已下架的設定區（去設定死路），改引導 Breeze。"""
    assert "請先到右上角「設定」設定" not in APP_JS
    assert "尚未設定 API key" not in APP_JS
    assert "去設定" not in APP_JS
    # 已刪除的隱藏設定欄位不得再被引用（引用了就是 querySelector null 炸掉）
    for removed_id in (
        "settings-gemini-key",
        "settings-openai-key",
        "settings-show-gemini",
        "settings-show-openai",
        "settings-gemini-status",
        "settings-openai-status",
    ):
        assert removed_id not in APP_JS, f"app.js 仍引用已刪除的 #{removed_id}"
        assert removed_id not in INDEX_HTML, f"index.html 仍含已下架的 #{removed_id}"


def test_settings_modal_offers_breeze_radio():
    assert 'value="breeze"' in INDEX_HTML, "設定 modal 缺 breeze 選項"
    # 雲端 radio 已下架
    assert 'value="gemini"' not in INDEX_HTML
    assert 'value="openai"' not in INDEX_HTML


def test_cloud_phase_pills_removed():
    """雲端時代的「壓縮音檔→STT→重新切句」pills 已整組移除（HTML＋渲染 JS）。"""
    assert "phase-pill" not in INDEX_HTML
    assert "renderTranscribePhasePills" not in APP_JS
    # 進度條本體要留著（Breeze／whisper_mlx 都靠它）
    assert 'id="transcribe-progress"' in INDEX_HTML
    assert 'id="transcribe-fill"' in INDEX_HTML
    assert "computeOverallPercent" in APP_JS


def test_glossary_copy_teaches_transcribe_engine_not_gemini():
    assert "教轉錄引擎正確寫出人名、品牌名" in INDEX_HTML
