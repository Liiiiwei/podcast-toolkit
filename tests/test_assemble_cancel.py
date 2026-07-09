"""合成取消（assemble_job.cancel_job）測試。

重現並鎖住的 bug：點合成後按取消，後端 ffmpeg 沒被砍、state 卡在 running，
下次合成被「已有合成正在進行中」擋下。取消後 state 必須收回 idle。

用 `sh -c 'sleep …'` 當假 ffmpeg：會長跑、且忽略 _run_queue 追加的 -progress 旗標；
被 kill 後 stdout 收到 EOF，走正常收尾路徑。
"""
from __future__ import annotations

import threading
from pathlib import Path
from time import monotonic, sleep

import pytest

from podcast_toolkit.web import assemble_job as aj


@pytest.fixture(autouse=True)
def _reset_state():
    """每個 test 前後把模組級 state 歸零，避免互相污染。"""
    aj._reset()
    with aj._LOCK:
        aj._ACTIVE_PROC = None
    yield
    aj._reset()
    with aj._LOCK:
        if aj._ACTIVE_PROC is not None:
            try:
                aj._ACTIVE_PROC.kill()
            except OSError:
                pass
        aj._ACTIVE_PROC = None


def _fake_plan(tmp: Path, seconds: int = 30) -> dict:
    return {
        "output_kind": "yt",
        "out": tmp / "out.mp4",
        "tmp_out": tmp / "out.tmp.mp4",
        "cmd": ["sh", "-c", f"sleep {seconds}"],
        "cwd": None,
        "total_dur": 100.0,
        "prebake": [],
    }


def _wait_until(pred, timeout=5.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if pred():
            return True
        sleep(0.02)
    return False


def test_cancel_idle_returns_false():
    assert aj.cancel_job() is False


def test_cancel_running_kills_proc_and_returns_to_idle(tmp_path):
    plan = _fake_plan(tmp_path)
    # 模擬 start_job 已把 state 設成 running（不實際跑 prepare_assembly）
    aj._reset(state="running", queue=["yt"], current="yt", total=1)

    t = threading.Thread(target=aj._run_queue, args=([plan],), daemon=True)
    t.start()

    # 等 ffmpeg（假的 sleep）真的起來並被註冊
    assert _wait_until(lambda: aj._ACTIVE_PROC is not None), "proc 沒被註冊"
    assert aj.get_status()["state"] == "running"

    # 按取消
    assert aj.cancel_job() is True

    t.join(timeout=5.0)
    assert not t.is_alive(), "coordinator 沒有結束"

    st = aj.get_status()
    assert st["state"] == "idle", f"取消後 state 應收回 idle，實際 {st['state']}"
    assert st["cancelled"] is False
    with aj._LOCK:
        assert aj._ACTIVE_PROC is None
    # 收尾有清掉 tmp，不留半成品
    assert not (tmp_path / "out.tmp.mp4").exists()


def test_after_cancel_can_start_again(tmp_path):
    """取消後 state=idle → start_job 的『已有合成正在進行中』守衛不該再擋。"""
    plan = _fake_plan(tmp_path)
    aj._reset(state="running", queue=["yt"], current="yt", total=1)
    t = threading.Thread(target=aj._run_queue, args=([plan],), daemon=True)
    t.start()
    assert _wait_until(lambda: aj._ACTIVE_PROC is not None)

    aj.cancel_job()
    t.join(timeout=5.0)

    # 不再是 running → 再次啟動的守衛條件為 False
    with aj._LOCK:
        blocked = aj._STATE["state"] == "running"
    assert blocked is False
