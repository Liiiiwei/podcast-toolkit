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

from podcast_toolkit.assemble import AssembleError, prepare_assembly
from podcast_toolkit.episode import Episode


# 模組級 state，build_app 每次都拿同一個 dict
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "state": "idle",  # idle | running | done | error
    "percent": 0.0,
    "eta_s": None,
    "out_path": None,
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
            "percent": 0.0,
            "eta_s": None,
            "out_path": None,
            "error": None,
            "started_at": None,
        })
        _STATE.update(kwargs)


def _set(**kwargs) -> None:
    with _LOCK:
        _STATE.update(kwargs)


def start_job(ep: Episode, force: bool = False) -> dict[str, Any]:
    with _LOCK:
        if _STATE["state"] == "running":
            raise RuntimeError("已有合成正在進行中")

    plan = prepare_assembly(ep.dir, output_kind="yt", force=force)

    _reset(
        state="running",
        percent=0.0,
        eta_s=None,
        out_path=str(plan["out"]),
        started_at=monotonic(),
    )

    cmd = list(plan["cmd"]) + ["-progress", "pipe:1", "-nostats"]
    proc = Popen(cmd, cwd=plan["cwd"], stdout=PIPE, stderr=PIPE,
                 text=True, bufsize=1)

    t = threading.Thread(
        target=_pump_progress,
        args=(proc, plan["total_dur"], plan["out"], plan["tmp_out"]),
        daemon=True,
    )
    t.start()
    return {"out_path": str(plan["out"]), "total_dur": plan["total_dur"]}


def _pump_progress(proc: Popen, total_dur: float, out_path: Path,
                   tmp_out: Path) -> None:
    """讀 ffmpeg -progress pipe:1，算 percent + ETA；成功 rename，失敗清 tmp。"""
    started = monotonic()
    last_out_time_us = 0

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
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

    stderr_tail = ""
    try:
        assert proc.stderr is not None
        stderr_tail = proc.stderr.read() or ""
    except Exception:
        pass

    returncode = proc.wait()
    if returncode == 0 and tmp_out.exists():
        # 成功才覆寫舊輸出
        tmp_out.replace(out_path)
        _set(state="done", percent=100.0, eta_s=0)
    else:
        # 失敗清 tmp，保留舊 out
        try:
            if tmp_out.exists():
                tmp_out.unlink()
        except OSError:
            pass
        tail = "\n".join((stderr_tail or "").strip().splitlines()[-5:])
        _set(
            state="error",
            error=f"ffmpeg 結束碼 {returncode}：{tail}" if tail else f"ffmpeg 結束碼 {returncode}",
        )
