"""背景跑 ffmpeg 合成 + 解 -progress pipe:1 餵給前端 poll。

模組層級單一 job slot：同時間只能跑一條 ffmpeg。
前端流程：POST /api/assemble → start_job() → 每秒 GET /api/assemble/status。
"""
from __future__ import annotations

import threading
from pathlib import Path
from subprocess import PIPE, Popen
from time import monotonic
from typing import Any

from podcast_toolkit.assemble import prepare_assembly
from podcast_toolkit.episode import Episode


# ffmpeg -progress 正常每秒都有輸出；超過這個秒數沒動靜視為卡死，強制終止
FFMPEG_STALL_TIMEOUT_S = 120

# 模組級 state，build_app 每次都拿同一個 dict
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "state": "idle",           # idle | running | done | error
    "queue": [],               # 例如 ["yt", "reels"]
    "current": None,           # 目前在跑的 target
    "index": 0,                # 第幾個（0-based）
    "total": 0,                # queue 長度
    "percent": 0.0,
    "eta_s": None,
    "out_path": None,          # 目前這個 target 的輸出
    "output_files": [],        # 已完成的輸出路徑 list
    "error": None,
    "started_at": None,
}


def get_status() -> dict[str, Any]:
    """前端 poll 用。回傳一份 snapshot 避免 race。"""
    with _LOCK:
        return dict(_STATE)


def _reset(**kwargs) -> None:
    with _LOCK:
        _STATE.update({
            "state": "idle",
            "queue": [],
            "current": None,
            "index": 0,
            "total": 0,
            "percent": 0.0,
            "eta_s": None,
            "out_path": None,
            "output_files": [],
            "error": None,
            "started_at": None,
        })
        _STATE.update(kwargs)


def _set(**kwargs) -> None:
    with _LOCK:
        _STATE.update(kwargs)


def start_job(
    ep: Episode,
    targets: list[str],
    force: bool = False,
    preview_sec: int | None = None,
) -> dict[str, Any]:
    """開新 job；targets 例如 ['yt', 'reels']。preview_sec 非 None → 走預覽模式（截斷 + .preview 檔名）。"""
    if not targets:
        raise ValueError("targets 不能為空")
    for t in targets:
        if t not in ("yt", "reels"):
            raise ValueError(f"未知 target={t}")

    with _LOCK:
        if _STATE["state"] == "running":
            raise RuntimeError("已有合成正在進行中")

    # 預先檢查所有 target：任一失敗就整批拒絕（不要跑一半才報錯）
    plans = []
    for t in targets:
        plans.append(prepare_assembly(
            ep.dir, output_kind=t, force=force, preview_sec=preview_sec,
        ))

    _reset(
        state="running",
        queue=list(targets),
        current=targets[0],
        index=0,
        total=len(targets),
        percent=0.0,
        eta_s=None,
        out_path=str(plans[0]["out"]),
        output_files=[],
        started_at=monotonic(),
    )

    coordinator = threading.Thread(
        target=_run_queue,
        args=(plans,),
        daemon=True,
    )
    coordinator.start()

    return {
        "targets": list(targets),
        "out_paths": [str(p["out"]) for p in plans],
    }


def _run_queue(plans: list[dict]) -> None:
    """coordinator：依序跑 plans，任一失敗就停止後續。"""
    for i, plan in enumerate(plans):
        _set(
            current=plan["output_kind"],
            index=i,
            out_path=str(plan["out"]),
            percent=0.0,
            eta_s=None,
        )
        cmd = list(plan["cmd"]) + ["-progress", "pipe:1", "-nostats"]
        proc = Popen(cmd, cwd=plan["cwd"], stdout=PIPE, stderr=PIPE,
                     text=True, bufsize=1)
        _pump_progress(proc, plan["total_dur"], plan["out"], plan["tmp_out"])

        # _pump_progress 失敗會 set state=error；成功只更新 percent，
        # 最終 done 由這裡統一設——否則多 target 間隙會被前端 poll 到假的 done。
        with _LOCK:
            if _STATE["state"] == "error":
                return  # 中止後續
            _STATE["output_files"].append(str(plan["out"]))
    # 全部跑完
    _set(state="done", percent=100.0, eta_s=0)


def _pump_progress(proc: Popen, total_dur: float, out_path: Path,
                   tmp_out: Path) -> None:
    """讀 ffmpeg -progress pipe:1，算 percent + ETA；成功 rename，失敗清 tmp。

    watchdog：-progress 太久沒輸出（pipe 卡住/ffmpeg 死鎖）就 kill 掉，
    讓 stdout 收到 EOF、走正常的失敗路徑，前端才不會永遠 poll 到 running。
    """
    started = monotonic()
    last_out_time_us = 0
    heartbeat = [monotonic()]
    stalled = [False]
    wd_stop = threading.Event()

    def _watchdog() -> None:
        while not wd_stop.wait(5.0):
            if monotonic() - heartbeat[0] > FFMPEG_STALL_TIMEOUT_S:
                stalled[0] = True
                try:
                    proc.kill()
                except OSError:
                    pass
                return

    threading.Thread(target=_watchdog, daemon=True).start()

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            heartbeat[0] = monotonic()
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key == "out_time_us":
                try:
                    last_out_time_us = int(value)
                except ValueError:
                    continue
                current_s = last_out_time_us / 1_000_000
                percent = min(100.0, (current_s / total_dur) * 100.0) if total_dur > 0 else 0.0
                elapsed = monotonic() - started
                if percent > 1.0:
                    eta_s = max(0, int(elapsed * (100.0 - percent) / percent))
                else:
                    eta_s = None
                _set(percent=percent, eta_s=eta_s)
            elif key == "progress" and value == "end":
                _set(percent=100.0, eta_s=0)
    except Exception as e:
        _set(error=f"讀取進度失敗：{e}")
    finally:
        wd_stop.set()

    stderr_tail = ""
    try:
        assert proc.stderr is not None
        stderr_tail = proc.stderr.read() or ""
    except Exception:
        pass

    returncode = proc.wait()
    if returncode == 0 and tmp_out.exists():
        # 成功才覆寫舊輸出；最終 state=done 由 _run_queue 統一設
        tmp_out.replace(out_path)
        _set(percent=100.0, eta_s=0)
    else:
        # 失敗清 tmp，保留舊 out
        try:
            if tmp_out.exists():
                tmp_out.unlink()
        except OSError:
            pass
        if stalled[0]:
            msg = f"ffmpeg 超過 {FFMPEG_STALL_TIMEOUT_S}s 沒有進度輸出，已強制終止（疑似卡死）"
        else:
            tail = "\n".join((stderr_tail or "").strip().splitlines()[-5:])
            msg = f"ffmpeg 結束碼 {returncode}：{tail}" if tail else f"ffmpeg 結束碼 {returncode}"
        _set(state="error", error=msg)
