"""轉錄 job 生命週期（watchdog 卡死偵測 + 取消）測試。

鎖住的兩個 bug：
1. H1 心跳架空 watchdog：舊實作「Breeze 子行程活著就無條件送心跳」，
   子行程 hang 死（活著但無輸出）時 watchdog 永遠不觸發、前端永遠顯示轉錄中。
   現在心跳只由真實進度（tqdm %）驅動，模型載入期有獨立 STARTUP_GRACE_S。
2. H2 無取消機制：watchdog 棄世代後子行程照樣活著繼續寫檔、與新 job 互踩。
   現在 watchdog / cancel_job / 新 job 搶 slot 都會 terminate 子行程。

假 Breeze = tmp 資料夾放一個假 make_subtitle.py（hang 版睡死不輸出；
進度版週期性把 tqdm 格式的 % 寫到 stderr），monkeypatch _breeze_dir 指過去。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from podcast_toolkit.episode import Episode
from podcast_toolkit.web import transcribe_job as tj

# hang 版：子行程活著但完全無輸出（模擬 Breeze 卡死）
HANG_SCRIPT = "import time\ntime.sleep(120)\n"

# 進度版：每 0.05s 對 stderr 刷一筆 tqdm 格式的 %，跑完正常退出
PROGRESS_SCRIPT = (
    "import sys, time\n"
    "for i in range(1, 16):\n"
    "    sys.stderr.write(' %d%%|#####  | x/y\\r' % (i * 6))\n"
    "    sys.stderr.flush()\n"
    "    time.sleep(0.05)\n"
)


@pytest.fixture(autouse=True)
def _reset_job_state():
    """每個 test 前後把模組級 state 歸零，並確保不留活的子行程 / worker。"""
    tj._reset()
    with tj._LOCK:
        tj._ACTIVE_PROC = None
        tj._WORKER = None
        tj._PROGRESS_SEEN = False
        tj._HEARTBEAT = 0.0
    yield
    with tj._LOCK:
        proc = tj._ACTIVE_PROC
        worker = tj._WORKER
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    if worker is not None:
        worker.join(timeout=5.0)
    tj._reset()
    with tj._LOCK:
        tj._ACTIVE_PROC = None
        tj._WORKER = None


def _fake_breeze_dir(tmp_path: Path, script: str) -> Path:
    bdir = tmp_path / "fake_breeze"
    bdir.mkdir(exist_ok=True)
    (bdir / "make_subtitle.py").write_text(script, encoding="utf-8")
    return bdir


def _wait_until(pred, timeout=5.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if pred():
            return True
        sleep(0.02)
    return False


def _active_proc():
    with tj._LOCK:
        return tj._ACTIVE_PROC


def _start_breeze(monkeypatch, tmp_path, ep_dir, script):
    """monkeypatch 假 Breeze 專案並啟動 job，回傳 (ep, 子行程)。"""
    bdir = _fake_breeze_dir(tmp_path, script)
    monkeypatch.setattr(tj, "_breeze_dir", lambda: bdir)
    ep = Episode(ep_dir)
    tj.start_breeze_job(ep)
    assert _wait_until(lambda: _active_proc() is not None), "子行程沒被註冊到 slot"
    return ep, _active_proc()


# --- H1：watchdog 要抓得到「子行程活著但無輸出」 ---

def test_watchdog_kills_hung_breeze_proc(monkeypatch, tmp_path, tmp_episode_dir):
    """hang 死（無任何進度輸出）→ grace period 過後 get_status 翻 error 並終結子行程。"""
    monkeypatch.setattr(tj, "STARTUP_GRACE_S", 0.4)
    monkeypatch.setattr(tj, "STALL_TIMEOUT_S", 0.4)
    v2 = tmp_episode_dir / "03_成品" / "測試集_final_v2.srt"
    old_v2 = v2.read_bytes()

    _, proc = _start_breeze(monkeypatch, tmp_path, tmp_episode_dir, HANG_SCRIPT)
    with tj._LOCK:
        worker = tj._WORKER

    # 舊 bug：這裡永遠等不到 error（無條件心跳把 watchdog 架空）
    assert _wait_until(
        lambda: tj.get_status()["state"] == "error", timeout=5.0
    ), f"watchdog 沒觸發，status={tj.get_status()}"
    assert "逾時" in (tj.get_status()["error"] or "")

    # 子行程要被 terminate（舊 bug 之二：翻 error 後行程照樣活著）
    assert _wait_until(lambda: proc.poll() is not None), "子行程沒被終結"
    # worker 收尾返回、且不進 ingest（_v2.srt 原封不動）
    assert _wait_until(lambda: not worker.is_alive())
    assert v2.read_bytes() == old_v2
    # 終態維持 error，不被舊 worker 寫壞
    assert tj.get_status()["state"] == "error"


def test_watchdog_spares_proc_with_live_progress(monkeypatch, tmp_path, tmp_episode_dir):
    """有持續 tqdm 進度輸出 → 即使逾時值極小也不誤殺（心跳由真實進度驅動）。"""
    monkeypatch.setattr(tj, "STARTUP_GRACE_S", 1.0)
    monkeypatch.setattr(tj, "STALL_TIMEOUT_S", 1.0)

    _, proc = _start_breeze(monkeypatch, tmp_path, tmp_episode_dir, PROGRESS_SCRIPT)

    # 子行程約跑 0.75s；期間持續 poll，不該被 watchdog 翻掉
    while proc.poll() is None:
        st = tj.get_status()
        assert st["state"] == "running", f"有進度仍被誤殺：{st}"
        sleep(0.05)

    # 正常退出（rc=0），不是被 terminate（SIGTERM 會是 -15）
    assert proc.returncode == 0
    # 收尾後不管落在哪個終態，都不能是「逾時」
    assert _wait_until(lambda: tj.get_status()["state"] != "running")
    assert "逾時" not in (tj.get_status()["error"] or "")


def test_progress_seen_switches_watchdog_to_stall_limit(monkeypatch, tmp_episode_dir):
    """解析到真實 % 前用 STARTUP_GRACE_S、之後用 STALL_TIMEOUT_S。"""
    monkeypatch.setattr(tj, "STARTUP_GRACE_S", 10.0)
    monkeypatch.setattr(tj, "STALL_TIMEOUT_S", 1000.0)
    # 手動佔 slot（不跑真 worker）
    job = tj._grab_slot(state="running", mode="breeze", phase="breeze-asr")

    # 尚無進度：心跳撥回 grace 之外 → 翻 error
    with tj._LOCK:
        tj._HEARTBEAT = monotonic() - 11.0
    assert tj.get_status()["state"] == "error"

    # 有進度後：同樣的心跳延遲（11s > grace）但 < stall → 不翻
    job = tj._grab_slot(state="running", mode="breeze", phase="breeze-asr")
    tj._set_job(job, percent=42.0)
    with tj._LOCK:
        tj._HEARTBEAT = monotonic() - 11.0
    assert tj.get_status()["state"] == "running"


# --- H2：取消機制 ---

def test_cancel_idle_returns_false():
    assert tj.cancel_job() is False


def test_cancel_kills_proc_releases_slot(monkeypatch, tmp_path, tmp_episode_dir):
    """轉錄中取消 → 子行程被終結、state=cancelled、slot 釋放、worker 收尾、不殘留寫檔。"""
    v2 = tmp_episode_dir / "03_成品" / "測試集_final_v2.srt"
    old_v2 = v2.read_bytes()

    ep, proc = _start_breeze(monkeypatch, tmp_path, tmp_episode_dir, HANG_SCRIPT)
    with tj._LOCK:
        worker = tj._WORKER
    assert tj.get_status()["state"] == "running"

    assert tj.cancel_job() is True
    # cancel_job 回傳當下就該離開 running（同步收尾，前端不用等空窗）
    st = tj.get_status()
    assert st["state"] == "cancelled", f"取消後 state 應為 cancelled，實際 {st['state']}"
    assert st["error"] is None

    assert _wait_until(lambda: proc.poll() is not None), "子行程沒被終結"
    assert _wait_until(lambda: not worker.is_alive()), "worker 沒收尾"
    with tj._LOCK:
        assert tj._ACTIVE_PROC is None
    # 不殘留寫檔：worker 被廢世代後不進 ingest
    assert v2.read_bytes() == old_v2

    # slot 已釋放：立刻能開下一個 job（守衛只擋 running）
    tj.start_breeze_job(ep)
    assert tj.get_status()["state"] == "running"
    tj.cancel_job()  # 收乾淨


def test_grab_slot_terminates_stale_proc(tmp_path):
    """搶 slot 時上一世代殘留的子行程（watchdog 翻 error 後還活著）要被終結。"""
    stale = subprocess.Popen(["sleep", "30"])
    with tj._LOCK:
        tj._ACTIVE_PROC = stale
        tj._STATE["state"] = "error"  # 模擬 watchdog 已翻 error 但行程沒死透

    tj._grab_slot(state="running", mode="breeze", phase="breeze-asr")
    try:
        assert _wait_until(lambda: stale.poll() is not None), "殘留子行程沒被終結"
    finally:
        if stale.poll() is None:
            stale.kill()
        tj._reset()


# --- endpoint：POST /api/transcribe/cancel ---

def test_cancel_endpoint(monkeypatch, tmp_path, tmp_episode_dir):
    from podcast_toolkit.web.api import build_app

    ep = Episode(tmp_episode_dir)
    client = TestClient(build_app(ep, shutdown=lambda: None))

    # 沒 job 在跑：冪等回 200、cancelled=False
    r = client.post("/api/transcribe/cancel")
    assert r.status_code == 200
    assert r.json()["cancelled"] is False

    # 起一個 hang 的 Breeze job 再從 endpoint 取消
    bdir = _fake_breeze_dir(tmp_path, HANG_SCRIPT)
    monkeypatch.setattr(tj, "_breeze_dir", lambda: bdir)
    r = client.post("/api/transcribe/breeze", json={})
    assert r.status_code == 202
    assert _wait_until(lambda: _active_proc() is not None)
    proc = _active_proc()

    r = client.post("/api/transcribe/cancel")
    body = r.json()
    assert body["ok"] is True
    assert body["cancelled"] is True
    assert body["state"] == "cancelled"
    assert client.get("/api/transcribe/status").json()["state"] == "cancelled"
    assert _wait_until(lambda: proc.poll() is not None), "子行程沒被終結"
