"""轉字幕取消（transcribe_job.cancel_job）測試。

鎖住的 bug：轉字幕時按「取消」只關 modal，後端 Breeze 子行程照跑、state 卡 running，
下次轉字幕被「已有轉字幕正在進行中」擋下。取消後：子行程要被砍、state 收回 idle、
被作廢的舊 worker 之後的寫入（含它因 kill 產生的 error）都要被丟棄。

用 `sh -c 'sleep …'` 當假 Breeze 子行程：長跑、被 kill 後 returncode 非 0。
"""
from __future__ import annotations

import subprocess
from time import monotonic, sleep

import pytest

from podcast_toolkit.web import transcribe_job as tj


@pytest.fixture(autouse=True)
def _reset_state():
    """每個 test 前後把模組級 state 歸零，避免互相污染。"""
    tj._reset()
    with tj._LOCK:
        tj._ACTIVE_PROC = None
        tj._CURRENT_JOB = 0
    yield
    with tj._LOCK:
        if tj._ACTIVE_PROC is not None:
            try:
                tj._ACTIVE_PROC.kill()
            except OSError:
                pass
        tj._ACTIVE_PROC = None
    tj._reset()


def _fake_running_proc(seconds: int = 30) -> subprocess.Popen:
    """模擬在跑的 Breeze 子行程，並註冊給 cancel_job。"""
    proc = subprocess.Popen(["sh", "-c", f"sleep {seconds}"])
    with tj._LOCK:
        tj._CURRENT_JOB += 1
        tj._ACTIVE_PROC = proc
    tj._reset(state="running", mode="breeze")
    return proc


def _wait_until(pred, timeout=5.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if pred():
            return True
        sleep(0.02)
    return False


def test_cancel_idle_returns_false():
    assert tj.cancel_job() is False


def test_cancel_running_kills_proc_and_returns_to_idle():
    proc = _fake_running_proc()
    assert tj.get_status()["state"] == "running"

    assert tj.cancel_job() is True

    # 子行程真的被砍
    assert _wait_until(lambda: proc.poll() is not None), "子行程沒被 kill"
    assert proc.returncode != 0

    st = tj.get_status()
    assert st["state"] == "idle", f"取消後 state 應收回 idle，實際 {st['state']}"
    assert st["error"] is None
    with tj._LOCK:
        assert tj._ACTIVE_PROC is None


def test_cancel_is_synchronous_idle_on_return():
    """cancel_job 回傳當下 state 必已是 idle（同步收回），前端才能立刻重按不撞 409。"""
    _fake_running_proc()
    assert tj.cancel_job() is True
    assert tj.get_status()["state"] == "idle", "cancel_job 回傳時就該是 idle"


def test_after_cancel_can_start_again():
    """取消後 state=idle → start 的『已有轉字幕正在進行中』守衛不該再擋。"""
    _fake_running_proc()
    tj.cancel_job()
    with tj._LOCK:
        blocked = tj._STATE["state"] == "running"
    assert blocked is False


def test_cancel_bumps_generation_so_stale_worker_writes_are_dropped():
    """取消後舊 worker 甦醒（例如被 kill 觸發的 error）寫入必須被丟棄，不污染 idle。"""
    _fake_running_proc()
    with tj._LOCK:
        old_job = tj._CURRENT_JOB

    assert tj.cancel_job() is True

    # 世代已 +1，舊 worker 用 old_job 寫 error 應被丟棄
    with tj._LOCK:
        assert tj._CURRENT_JOB == old_job + 1
    tj._set_job(old_job, state="error", error="被砍的 worker 事後回報")

    st = tj.get_status()
    assert st["state"] == "idle", "舊 worker 的 error 寫入不該蓋掉收回的 idle"
    assert st["error"] is None
