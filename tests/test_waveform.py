"""字幕卡時間軸波形：peaks 計算、sidecar 快取、/api/waveform 路由。"""
import json
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from podcast_toolkit import waveform
from podcast_toolkit.episode import Episode
from podcast_toolkit.web.api import build_app


def _pcm(samples: list[int]) -> bytes:
    """把 s16 整數樣本清單打包成 s16le PCM bytes（與 ffmpeg -f s16le 對齊）。"""
    return struct.pack(f"<{len(samples)}h", *samples)


# ---- compute_peaks（純函式，無 ffmpeg）----

def test_compute_peaks_single_bucket_peak():
    # sr 8000、20ms → 每桶 160 樣本；整桶 16384 → 16384/32768*100 = 50
    peaks = waveform.compute_peaks(_pcm([16384] * 160))
    assert peaks == [50]


def test_compute_peaks_two_buckets():
    pcm = _pcm([8192] * 160 + [32767] * 160)  # 兩滿桶：25、100
    assert waveform.compute_peaks(pcm) == [25, 100]


def test_compute_peaks_uses_abs_max():
    # 桶內有 -32768（絕對值最大）→ 峰值頂到 100
    peaks = waveform.compute_peaks(_pcm([0] * 159 + [-32768]))
    assert peaks == [100]


def test_compute_peaks_partial_tail_kept():
    # 160 滿桶 + 40 殘尾 → 收 2 桶（成品時長多半非整桶）
    peaks = waveform.compute_peaks(_pcm([100] * 160 + [30000] * 40))
    assert len(peaks) == 2 and peaks[1] == round(30000 / 32768 * 100)


def test_compute_peaks_empty_input():
    assert waveform.compute_peaks(b"") == []


def test_compute_peaks_odd_trailing_byte_no_crash():
    # 尾端多半個樣本（奇數 byte）→ 不可讓 audioop 拋錯，殘半棄掉
    peaks = waveform.compute_peaks(_pcm([16384] * 160) + b"\x01")
    assert peaks == [50]


# ---- build_waveform + sidecar 快取 ----

@pytest.fixture
def src_audio(tmp_path: Path) -> Path:
    p = tmp_path / "master.m4a"
    p.write_bytes(b"AUDIO-SRC")
    return p


def _stub_decode(monkeypatch, samples: list[int], calls: list):
    def fake(target, **kw):
        calls.append(target)
        return _pcm(samples)
    monkeypatch.setattr(waveform, "_decode_pcm", fake)


def test_build_waveform_computes_and_caches(monkeypatch, src_audio, tmp_path):
    _stub_decode(monkeypatch, [16384] * 160 + [32767] * 160, [])
    monkeypatch.setattr(
        waveform.silencedetect, "detect_silence_intervals",
        lambda *a, **k: [(1.0, 1.5)],
    )
    cache = tmp_path / "wf.json"
    data = waveform.build_waveform(src_audio, cache)

    assert data["peaks"] == [50, 100]
    assert data["silences"] == [[1.0, 1.5]]
    assert data["duration"] == round(2 * waveform._BUCKET_MS / 1000.0, 3)
    assert data["src"] == "master.m4a"
    # sidecar 真的落檔且可解析
    assert cache.exists()
    assert json.loads(cache.read_text(encoding="utf-8"))["peaks"] == [50, 100]


def test_build_waveform_cache_hit_skips_decode(monkeypatch, src_audio, tmp_path):
    monkeypatch.setattr(
        waveform.silencedetect, "detect_silence_intervals", lambda *a, **k: []
    )
    calls: list = []
    _stub_decode(monkeypatch, [16384] * 160, calls)
    cache = tmp_path / "wf.json"
    first = waveform.build_waveform(src_audio, cache)
    assert len(calls) == 1

    # 第二次：命中快取 → 不得再解碼
    def boom(*a, **k):
        raise AssertionError("cache 命中卻又解碼了")
    monkeypatch.setattr(waveform, "_decode_pcm", boom)
    assert waveform.build_waveform(src_audio, cache) == first


def test_build_waveform_recomputes_on_src_change(monkeypatch, src_audio, tmp_path):
    monkeypatch.setattr(
        waveform.silencedetect, "detect_silence_intervals", lambda *a, **k: []
    )
    cache = tmp_path / "wf.json"
    _stub_decode(monkeypatch, [8192] * 160, [])
    assert waveform.build_waveform(src_audio, cache)["peaks"] == [25]

    # 換檔：size 變（簽章的一部分）→ 快取必須失效重算
    src_audio.write_bytes(b"AUDIO-SRC-DIFFERENT-SIZE")
    _stub_decode(monkeypatch, [32767] * 160, [])
    assert waveform.build_waveform(src_audio, cache)["peaks"] == [100]


def test_read_cache_bad_version_returns_none(src_audio, tmp_path):
    cache = tmp_path / "wf.json"
    sig = waveform._src_signature(src_audio)
    cache.write_text(json.dumps({
        "version": waveform._CACHE_VERSION + 999,   # 舊版格式
        "src": src_audio.name, "src_mtime": sig[0], "src_size": sig[1],
    }), encoding="utf-8")
    assert waveform._read_cache(cache, sig, src_audio.name) is None


def test_read_cache_corrupt_json_returns_none(src_audio, tmp_path):
    cache = tmp_path / "wf.json"
    cache.write_text("{ not json", encoding="utf-8")
    sig = waveform._src_signature(src_audio)
    assert waveform._read_cache(cache, sig, src_audio.name) is None


# ---- /api/waveform 路由（不實跑 ffmpeg：build_waveform 用替身）----

@pytest.fixture
def client_with_audio(tmp_episode_dir: Path):
    (tmp_episode_dir / "01_母帶" / "測試集.m4a").write_bytes(b"FAKE-AUDIO")
    ep = Episode(tmp_episode_dir)
    return TestClient(build_app(ep, shutdown=lambda: None))


def test_waveform_route_returns_json_and_uses_main_audio(monkeypatch, client_with_audio):
    seen = {}

    def fake_build(target: Path, cache: Path):
        seen["target"] = target
        seen["cache"] = cache
        return {"peaks": [10, 20], "silences": [[0.5, 0.9]], "duration": 0.04}
    monkeypatch.setattr(waveform, "build_waveform", fake_build)

    r = client_with_audio.get("/api/waveform")
    assert r.status_code == 200
    body = r.json()
    assert body["peaks"] == [10, 20] and body["silences"] == [[0.5, 0.9]]
    # 預設用主音訊（單軌母帶），快取落在 04_工作檔/
    assert seen["target"].name == "測試集.m4a"
    assert seen["cache"].parent.name == "04_工作檔"
    assert seen["cache"].name == "測試集_waveform_測試集.json"


def test_waveform_route_404_when_no_audio(tmp_episode_dir: Path):
    # 01_母帶 只有 mp4（非 m4a/mp3/wav）→ main_audio 找不到 → 404
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"V")
    ep = Episode(tmp_episode_dir)
    client = TestClient(build_app(ep, shutdown=lambda: None))
    r = client.get("/api/waveform")
    assert r.status_code == 404


def test_waveform_route_path_override(monkeypatch, tmp_episode_dir: Path):
    (tmp_episode_dir / "01_母帶" / "外接.wav").write_bytes(b"W")
    ep = Episode(tmp_episode_dir)
    client = TestClient(build_app(ep, shutdown=lambda: None))
    seen = {}

    def fake_build(target: Path, cache: Path):
        seen["t"] = target
        return {"peaks": []}
    monkeypatch.setattr(waveform, "build_waveform", fake_build)

    r = client.get("/api/waveform", params={"path": "01_母帶/外接.wav"})
    assert r.status_code == 200
    assert seen["t"].name == "外接.wav"


def test_waveform_route_path_escape_rejected(client_with_audio):
    r = client_with_audio.get("/api/waveform", params={"path": "../../etc/passwd"})
    assert r.status_code == 400


def test_waveform_route_path_not_found(client_with_audio):
    r = client_with_audio.get("/api/waveform", params={"path": "01_母帶/不存在.wav"})
    assert r.status_code == 404
