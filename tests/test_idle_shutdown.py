"""idle-shutdown 機制單元測試。

涵蓋：
- should_idle_shutdown 純函式（三個主要情境）
- POST /api/heartbeat 更新時間戳（透過 app.state.last_heartbeat 直接驗證）
- watchdog 防重複啟動（build_app 呼叫兩次只建立一條 watchdog thread）
- 端到端 smoke test：真實 uvicorn server，無心跳→自關 / 有心跳→不關
"""
from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient

import podcast_toolkit.web.api as api_mod
from podcast_toolkit.web.api import build_app, should_idle_shutdown


# ── should_idle_shutdown 純函式測試 ───────────────────────────────


def test_no_active_job_over_threshold_returns_true():
    """無 active job 且超過閾值 → 應關閉。"""
    assert should_idle_shutdown(
        last_heartbeat_ts=0.0,
        now_ts=100.0,
        has_active_job=False,
        idle_threshold_sec=90.0,
    ) is True


def test_has_active_job_over_threshold_returns_false():
    """有 active job，即使超過閾值 → 不關閉。"""
    assert should_idle_shutdown(
        last_heartbeat_ts=0.0,
        now_ts=100.0,
        has_active_job=True,
        idle_threshold_sec=90.0,
    ) is False


def test_under_threshold_returns_false():
    """未超過閾值 → 不關閉（無論是否有 job）。"""
    assert should_idle_shutdown(
        last_heartbeat_ts=0.0,
        now_ts=50.0,
        has_active_job=False,
        idle_threshold_sec=90.0,
    ) is False


def test_exactly_at_threshold_returns_true():
    """恰好等於閾值（>=）→ 應關閉。"""
    assert should_idle_shutdown(
        last_heartbeat_ts=0.0,
        now_ts=90.0,
        has_active_job=False,
        idle_threshold_sec=90.0,
    ) is True


# ── POST /api/heartbeat 更新時間戳測試 ────────────────────────────


def test_heartbeat_endpoint_returns_204():
    """POST /api/heartbeat 應回 204。"""
    app = build_app(ep=None, shutdown=lambda: None)
    client = TestClient(app)
    r = client.post("/api/heartbeat")
    assert r.status_code == 204


def test_heartbeat_updates_timestamp():
    """打 /api/heartbeat 後，app.state.last_heartbeat 必須被更新。

    真斷言：打前記下時間戳，打後讀 app.state.last_heartbeat，
    確認數值有推進（≥ 打前的值）。若 endpoint 不更新時間戳，
    last_heartbeat 仍是初始化時的值（早於 before_ts），測試會 fail。
    """
    from time import monotonic

    app = build_app(ep=None, shutdown=lambda: None)
    client = TestClient(app)

    before_ts = monotonic()
    r = client.post("/api/heartbeat")
    assert r.status_code == 204

    after_heartbeat_ts = app.state.last_heartbeat
    assert after_heartbeat_ts >= before_ts, (
        f"heartbeat endpoint 沒有更新 app.state.last_heartbeat："
        f"before={before_ts:.6f}, state={after_heartbeat_ts:.6f}"
    )


def test_watchdog_no_duplicate_threads():
    """build_app 呼叫兩次，第二次呼叫不應再新增 watchdog thread。

    策略：重置旗標 → 記錄 before 數量 → 呼叫兩次 build_app
    → after 數量應只比 before 多 1（而非多 2）。
    """
    # 重置旗標，讓防重複機制從頭開始計數
    with api_mod._watchdog_lock:
        api_mod._watchdog_started = False

    before_count = sum(
        1 for t in threading.enumerate() if t.name == "idle-watchdog"
    )

    build_app(ep=None, shutdown=lambda: None)  # 第一次：應啟動 1 條
    build_app(ep=None, shutdown=lambda: None)  # 第二次：被防重複機制擋住

    after_count = sum(
        1 for t in threading.enumerate() if t.name == "idle-watchdog"
    )
    new_threads = after_count - before_count
    assert new_threads == 1, (
        f"兩次 build_app 應只新增 1 條 watchdog，實際新增 {new_threads} 條"
    )


# ── 端到端 smoke test（真實 uvicorn Server）──────────────────────────


def _free_port() -> int:
    """找一個空的 localhost port。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_e2e_server(
    idle_threshold_sec: float,
    check_interval_sec: float,
) -> tuple[uvicorn.Server, threading.Thread]:
    """建立並在背景 thread 啟動一個真實 uvicorn.Server。

    回傳 (server, thread)。呼叫端負責設 server.should_exit + join(timeout) 清理。
    watchdog 全域旗標在這裡重置，允許每個測試獨立啟動一條 watchdog。
    """
    # 重置防重複旗標，讓這個測試的 build_app 能啟動自己的 watchdog
    with api_mod._watchdog_lock:
        api_mod._watchdog_started = False

    port = _free_port()

    # shutdown callback：讓 watchdog 觸發時能真正關掉 uvicorn
    server_holder: dict = {"instance": None}

    def shutdown_cb() -> None:
        if server_holder["instance"] is not None:
            server_holder["instance"].should_exit = True

    app = build_app(
        ep=None,
        shutdown=shutdown_cb,
        _idle_threshold_sec=idle_threshold_sec,
        _idle_check_interval_sec=check_interval_sec,
    )

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="critical",  # 測試期間抑制 uvicorn 輸出
    )
    server = uvicorn.Server(config)
    server_holder["instance"] = server

    t = threading.Thread(target=server.run, daemon=True, name=f"e2e-server-{port}")
    t.start()

    # 等 uvicorn 完成 startup（最多 3 秒）
    deadline = time.monotonic() + 3.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)

    return server, t


def test_e2e_no_heartbeat_server_shuts_down():
    """端到端：無心跳情況下，server 應在短時間內自我關閉（should_exit=True）。

    使用極短閾值（0.3s）與檢查間隔（0.1s），最多等 5 秒。
    """
    THRESHOLD = 0.3   # 秒：超過此時間無心跳即觸發關閉
    INTERVAL = 0.1    # 秒：watchdog 檢查週期
    WAIT_MAX = 5.0    # 秒：測試等待上限

    server, t = _make_e2e_server(
        idle_threshold_sec=THRESHOLD,
        check_interval_sec=INTERVAL,
    )

    try:
        # 不打任何心跳，等待 server.should_exit 變 True
        deadline = time.monotonic() + WAIT_MAX
        while not server.should_exit and time.monotonic() < deadline:
            time.sleep(0.05)

        assert server.should_exit, (
            f"無心跳 {THRESHOLD}s + watchdog 間隔 {INTERVAL}s，"
            f"等待 {WAIT_MAX}s 後 server.should_exit 仍是 False"
        )
    finally:
        # 確保 server thread 乾淨退出
        server.should_exit = True
        t.join(timeout=3.0)


def test_e2e_with_heartbeat_server_stays_alive():
    """端到端（對照）：持續心跳時，server 在同等時間窗內不應自我關閉。

    同樣短參數（閾值 0.3s、間隔 0.1s），但測試期間每 0.05s 更新一次心跳時間戳。
    驗證期 1.5s（> 4 個閾值），server 必須維持 should_exit=False。
    """
    THRESHOLD = 0.3    # 秒
    INTERVAL = 0.1     # 秒
    OBSERVE_SEC = 1.5  # 秒：觀察期（含多個 watchdog 週期）

    server, t = _make_e2e_server(
        idle_threshold_sec=THRESHOLD,
        check_interval_sec=INTERVAL,
    )

    try:
        # 取得 app 實例（由 uvicorn 持有；startup 完成後 server.app 可存取）
        app = server.config.app

        # 持續更新心跳：在觀察期內每 0.05s 刷新一次 last_heartbeat
        stop_heartbeat = threading.Event()

        def _keep_alive() -> None:
            from time import monotonic
            while not stop_heartbeat.wait(0.05):
                app.state.last_heartbeat = monotonic()

        hb_thread = threading.Thread(target=_keep_alive, daemon=True)
        hb_thread.start()

        time.sleep(OBSERVE_SEC)
        stop_heartbeat.set()
        hb_thread.join(timeout=1.0)

        assert not server.should_exit, (
            f"持續心跳期間（{OBSERVE_SEC}s），server 不應自我關閉，"
            f"但 should_exit={server.should_exit}"
        )
    finally:
        # 乾淨關閉 server thread
        server.should_exit = True
        t.join(timeout=3.0)
