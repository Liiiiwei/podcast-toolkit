"""合成進度卡在完成邊緣（assemble_job._run_queue）測試。

重現並鎖住的 bug：ffmpeg 已經成功完成、輸出檔也已經 rename 完成，但 _run_queue
在「送出最終 done」之前的收尾動作（sidecar 字幕寫檔等）拋出例外時，coordinator
thread 靜默死亡（Python thread 的預設行為：印 traceback 到 stderr、不往外傳、
不讓任何人知道）。因為 state="done" 只在整個 for 迴圈跑完後的最後一行設定，
coordinator 一旦死在迴圈中段，_STATE["state"] 就永遠停在 "running"——percent
已經是 100、輸出檔已經在正確位置，但前端會永遠 poll 到「running」，沒有任何
可見錯誤。更嚴重的是此時使用者按「取消」也救不回來：_ACTIVE_PROC 早已在
_pump_progress 成功收尾時被設回 None，_COORDINATOR 指向的 thread 物件已死但
cancel_job() 從不檢查 state 是否真的離開 running，join 一個已死的 thread 幾乎
瞬間返回、然後無條件回傳 True——job slot 永遠卡死，之後任何新合成都會被
「已有合成正在進行中」(409) 擋下，只能重開整個 app。

用 `sh -c` 產生符合 ffmpeg -progress pipe:1 格式的假輸出（out_time_us= / progress=end），
不需要真的 ffmpeg 或真的媒體檔。
"""
from __future__ import annotations

import threading
from pathlib import Path
from time import monotonic, sleep

import pytest

from podcast_toolkit.web import assemble_job as aj


@pytest.fixture(autouse=True)
def _reset_state():
    """每個 test 前後把模組級 state 歸零，避免互相污染（沿用 test_assemble_cancel.py 慣例）。"""
    aj._reset()
    with aj._LOCK:
        aj._ACTIVE_PROC = None
        aj._COORDINATOR = None
    yield
    aj._reset()
    with aj._LOCK:
        if aj._ACTIVE_PROC is not None:
            try:
                aj._ACTIVE_PROC.kill()
            except OSError:
                pass
        aj._ACTIVE_PROC = None
        aj._COORDINATOR = None


def _wait_until(pred, timeout=5.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if pred():
            return True
        sleep(0.02)
    return False


def _fake_ffmpeg_success_cmd() -> list[str]:
    """模擬 ffmpeg -progress pipe:1 吐出的最後幾行：跑到底＋正常結束（exit 0）。"""
    script = "printf 'out_time_us=1000000\\nprogress=end\\n'"
    return ["sh", "-c", script]


def test_exception_after_success_surfaces_as_error_not_stuck(tmp_path):
    """核心回歸測試：ffmpeg 成功 + 輸出已 rename，但收尾（sidecar 寫檔）拋例外時，
    state 必須可見地變成 error（而不是靜默卡死在 running 永不改變）。"""
    out = tmp_path / "out.mp4"
    tmp_out = tmp_path / "out.tmp.mp4"
    tmp_out.write_bytes(b"fake video bytes")  # 模擬 ffmpeg 已經寫好暫存檔

    # sidecar 目標路徑的父目錄不存在 → write_text 會丟 FileNotFoundError
    # （對應真實場景：外接硬碟斷線、權限被拒、磁碟已滿等 I/O 錯誤）
    bad_sidecar_path = tmp_path / "missing_dir" / "out.srt"
    plan = {
        "output_kind": "yt",
        "out": out,
        "tmp_out": tmp_out,
        "cmd": _fake_ffmpeg_success_cmd(),
        "cwd": None,
        "total_dur": 1.0,
        "prebake": [],
        "sidecar_srt": {"path": bad_sidecar_path, "content": "dummy srt content"},
    }
    aj._reset(state="running", queue=["yt"], current="yt", total=1)

    t = threading.Thread(target=aj._run_queue, args=([plan],), daemon=True)
    t.start()
    t.join(timeout=5.0)

    assert not t.is_alive(), "coordinator thread 應該已經結束"

    # 主影片其實已經跑完產出（rename 已完成）——修復後這個事實不能改變
    assert out.exists(), "ffmpeg 成功產出的最終檔應該已經 rename 完成"
    assert not tmp_out.exists()

    st = aj.get_status()
    # 修復後：收尾失敗必須讓 state 可見地變成 error，不能卡在 running 不動
    assert st["state"] == "error", (
        f"sidecar 寫檔失敗應該讓 state 變成 error（失敗路徑不准靜默），"
        f"實際 state={st['state']!r}"
    )
    assert st["error"], "error 欄位必須有訊息，讓前端能顯示具體原因"

    # job slot 必須確實釋放：state 已離開 running/preparing → 新合成不該被 409 擋下
    with aj._LOCK:
        blocked = aj._STATE["state"] in ("running", "preparing")
    assert blocked is False, "state=error 後 job slot 應已釋放，不該再擋新合成"


def test_cancel_after_coordinator_dies_force_recovers_state(tmp_path):
    """防禦性收尾：即使 coordinator 死於某個未被前一個測試涵蓋的未知例外，
    cancel_job() 事後仍必須把 state 從 running 救回來，不能無條件回 True 卻什麼都沒做。

    直接用 monkeypatch 模擬「coordinator thread 跑到一半就死了，但沒人設過 state=error」
    這個更廣義的情境（不特定綁在 sidecar 寫檔這一個成因上）。
    """
    def _dies_immediately(plans):
        raise RuntimeError("模擬任何未預期的內部例外")

    aj._reset(state="running", queue=["yt"], current="yt", total=1)
    t = threading.Thread(target=_dies_immediately, args=([{}],), daemon=True)
    with aj._LOCK:
        aj._COORDINATOR = t
    t.start()
    t.join(timeout=2.0)
    assert not t.is_alive()

    # 此時 state 仍是 running（沒有人碰過它）——這裡驗證 cancel_job 本身要有防禦力
    assert aj.get_status()["state"] == "running"

    cancelled = aj.cancel_job()
    st = aj.get_status()
    assert cancelled is True
    assert st["state"] != "running", (
        "cancel_job 發現 coordinator 已死但 state 還卡在 running 時，必須強制收回，"
        f"不能無條件回 True 卻放著 job slot 永久卡死（實際 state={st['state']!r}）"
    )
