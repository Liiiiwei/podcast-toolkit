"""edit.py 的「版本感知接管」決策測試。

覆蓋新裝 DMG 在別台電腦打開時的坑：機器上還有一個舊版 server 活著占著 lock，
新啟動若無腦沿用 → 瀏覽器被導去舊行程 → 前端誤報「執行的是舊版」＋90 秒後閒置關閉。
修法是：探既有 server 的 /api/version，版本相同才沿用，不同或探不到就關掉它、搶回 lock。
"""
import os
from pathlib import Path

from podcast_toolkit import edit, server_lock


# —— _should_reuse：純函式，版本相同才沿用 ——

def test_should_reuse_only_when_versions_match():
    assert edit._should_reuse("0.2.0+gABC.T1", "0.2.0+gABC.T1") is True
    assert edit._should_reuse("0.2.0+gOLD.T0", "0.2.0+gNEW.T1") is False
    # 探不到（連不上或舊後端無 /api/version）→ 一律接管
    assert edit._should_reuse(None, "0.2.0+gNEW.T1") is False


# —— _handle_existing_server：注入 probe/terminate/open_browser 驗決策分支 ——

def _spy():
    calls = {"probe": 0, "terminate": [], "open": []}
    return calls


def test_handle_existing_reuses_same_version(monkeypatch):
    monkeypatch.setattr(edit, "_BUILD_ID", "0.2.0+gSAME.T1")
    calls = _spy()

    def probe(port):
        calls["probe"] += 1
        return "0.2.0+gSAME.T1"

    def terminate(pid):
        calls["terminate"].append(pid)
        return True

    out = edit._handle_existing_server(
        4321, 8000,
        probe=probe,
        terminate=terminate,
        open_browser=lambda url: calls["open"].append(url),
    )
    assert out == 0                       # 沿用 → 呼叫端 return 0
    assert calls["terminate"] == []       # 不該殺舊行程
    assert calls["open"] == ["http://127.0.0.1:8000"]


def test_handle_existing_takes_over_on_version_mismatch(monkeypatch):
    monkeypatch.setattr(edit, "_BUILD_ID", "0.2.0+gNEW.T2")
    calls = _spy()

    out = edit._handle_existing_server(
        4321, 8000,
        probe=lambda port: "0.2.0+gOLD.T1",
        terminate=lambda pid: calls["terminate"].append(pid) or True,
        open_browser=lambda url: calls["open"].append(url),
    )
    assert out is None                    # None → 呼叫端繼續起新 server
    assert calls["terminate"] == [4321]   # 舊行程被關
    assert calls["open"] == []            # 不開既有 instance


def test_handle_existing_takes_over_when_probe_unreachable(monkeypatch):
    monkeypatch.setattr(edit, "_BUILD_ID", "0.2.0+gNEW.T2")
    calls = _spy()

    out = edit._handle_existing_server(
        4321, 8000,
        probe=lambda port: None,          # 舊後端沒有 /api/version → 探不到
        terminate=lambda pid: calls["terminate"].append(pid) or True,
        open_browser=lambda url: calls["open"].append(url),
    )
    assert out is None
    assert calls["terminate"] == [4321]


def test_handle_existing_returns_error_when_terminate_fails(monkeypatch):
    monkeypatch.setattr(edit, "_BUILD_ID", "0.2.0+gNEW.T2")

    out = edit._handle_existing_server(
        4321, 8000,
        probe=lambda port: "0.2.0+gOLD.T1",
        terminate=lambda pid: False,      # 舊行程賴著不走
        open_browser=lambda url: None,
    )
    assert out == 1                       # 接管失敗 → 呼叫端 return 1（別硬起新 server）


# —— _terminate_and_wait：行程早就不在 → 清殘檔 lock 並回 True ——

def test_terminate_and_wait_clears_stale_lock_for_dead_pid(tmp_path: Path, monkeypatch):
    lock = tmp_path / ".server.lock"
    lock.write_text("99999998\n8000\n", encoding="utf-8")
    monkeypatch.setattr(edit, "LOCK_PATH", lock)
    # 極不可能存在的 pid：os.kill 直接丟 OSError → 視為已消失、清 lock
    assert edit._terminate_and_wait(99999998, timeout=1.0) is True
    assert not lock.exists()


def test_terminate_and_wait_kills_live_process(tmp_path: Path, monkeypatch):
    """對真的活著的行程送 SIGTERM，它結束後回 True。"""
    import subprocess
    import sys

    lock = tmp_path / ".server.lock"
    monkeypatch.setattr(edit, "LOCK_PATH", lock)
    # 起一個會被 SIGTERM 預設終止的短命子行程（sleep，預設不攔 SIGTERM）
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert edit._terminate_and_wait(child.pid, timeout=5.0) is True
    finally:
        if child.poll() is None:          # 保險：測試失敗也別留殭屍
            child.kill()
        child.wait()
