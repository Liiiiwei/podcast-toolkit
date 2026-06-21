"""背景跑 ffmpeg 合成 + 解 -progress pipe:1 餵給前端 poll。

模組層級單一 job slot：同時間只能跑一條 ffmpeg。
前端流程：POST /api/assemble → start_job() → 每秒 GET /api/assemble/status。
"""
from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from subprocess import PIPE, Popen
from time import monotonic
from typing import Any

from podcast_toolkit.assemble import (
    _leveled_proxy_valid,
    prepare_assembly,
    shift_srt,
    write_leveled_meta,
)
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
    subtitle_mode: str = "burn",
    overlay_srt: Path | None = None,
    overlay_shift_ms: int = 0,
    keep_all_content: bool = False,
) -> dict[str, Any]:
    """開新 job；targets 例如 ['yt', 'reels']。preview_sec 非 None → 走預覽模式（截斷 + .preview 檔名）。
    subtitle_mode：burn=燒字幕（預設）、sidecar=影片不燒+另存對齊 .srt、overlay=抽換字幕。
    overlay：把 overlay_srt（已對齊成品時間軸的字幕）整份提前 overlay_shift_ms 毫秒後，
    在合成最後一段直接燒上；鏡頭/刪段/倍速照舊，另存成新檔不蓋原成品。
    sidecar / overlay 只對 yt 生效；reels 一律硬燒（見下方 per-target 政策）。"""
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
        # 字幕模式 per-target 政策：sidecar / overlay（成品時間軸操作）只套在 yt；
        # reels（IG/TikTok/Shorts 不吃外掛 .srt）一律硬燒，否則成品完全沒字。
        # 核心 prepare_assembly 仍支援任意 (output_kind, subtitle_mode) 組合（CLI / 測試），
        # 這裡只是 web 單一字幕開關對應到各 target 的政策。
        eff_mode = subtitle_mode if t == "yt" else "burn"
        ov_srt: Path | None = None
        out_override: Path | None = None
        del_override: list | None = None
        if eff_mode == "overlay":
            if overlay_srt is None:
                raise ValueError("overlay 模式需要 overlay_srt")
            ov_srt = Path(overlay_srt)
            shift_tag = ""
            if overlay_shift_ms:
                # 「提前 N ms」= 整份時間軸 -N/1000 秒
                shifted = ep.subdir("work") / f"_overlay_shift_{overlay_shift_ms:+d}ms.srt"
                shift_srt(ov_srt, shifted, -overlay_shift_ms / 1000.0)
                ov_srt = shifted
                shift_tag = f"_{overlay_shift_ms:+d}ms"
            # 保留全部內容：不套 yaml 刪段，對齊外部字幕的完整時間軸（避免事後加的刪段
            # 讓外部字幕在刪點之後整段慢出現）。
            if keep_all_content:
                del_override = []
            # 另存成新檔名（含來源字幕名 + 位移），不蓋原 _YT完整版.mp4，也方便 A/B 比對
            out_name = f"{ep.name}_YT完整版_{Path(overlay_srt).stem}{shift_tag}.mp4"
            out_override = ep.subdir("output") / out_name
        plans.append(prepare_assembly(
            ep.dir, output_kind=t, force=force, preview_sec=preview_sec,
            subtitle_mode=eff_mode, overlay_srt=ov_srt, out_override=out_override,
            deletions_override=del_override,
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
        # 旋轉拉正預烤：主合成前先把需要的 proxy 建好（已建好 / 前一個 target 剛建好 → 跳過）。
        # 任一預烤失敗 → 中止整批（_run_prebake 已 set state=error）。
        for pb in plan.get("prebake", []):
            if _leveled_proxy_valid(pb["proxy"], pb["meta"], pb["src"], pb["angle"]):
                continue
            if not _run_prebake(pb):
                return
            # 預烤完回到該 target 的進度起點
            _set(current=plan["output_kind"], percent=0.0, eta_s=None)
        cmd = list(plan["cmd"]) + ["-progress", "pipe:1", "-nostats"]
        proc = Popen(cmd, cwd=plan["cwd"], stdout=PIPE, stderr=PIPE,
                     text=True, bufsize=1)
        _pump_progress(proc, plan["total_dur"], plan["out"], plan["tmp_out"])

        # _pump_progress 失敗會 set state=error；成功只更新 percent，
        # 最終 done 由這裡統一設——否則多 target 間隙會被前端 poll 到假的 done。
        with _LOCK:
            failed = _STATE["state"] == "error"
        if failed:
            return  # 中止後續

        # 成功：影片已 rename 完成。sidecar 模式才把對齊好的字幕 .srt 落在成品旁。
        outputs = [str(plan["out"])]
        sidecar = plan.get("sidecar_srt")
        if sidecar:
            sidecar["path"].write_text(sidecar["content"], encoding="utf-8")
            outputs.append(str(sidecar["path"]))
        with _LOCK:
            _STATE["output_files"].extend(outputs)
    # 全部跑完
    _set(state="done", percent=100.0, eta_s=0)


def _run_prebake(pb: dict) -> bool:
    """跑單一旋轉拉正預烤；成功 rename proxy + 寫 meta 回 True，失敗回 False（_pump_progress 已 set error）。

    與主合成共用 _pump_progress（tmp→proxy rename + watchdog + 進度）。proxy 是一次性快取，
    之後同角度的輸出都重用 → 這段 rotate 成本只在角度變動後付一次。"""
    proxy = Path(pb["proxy"])
    tmp = Path(pb["tmp"])
    _set(current=pb.get("label", "旋轉拉正"), percent=0.0, eta_s=None)
    cmd = list(pb["cmd"]) + ["-progress", "pipe:1", "-nostats"]
    proc = Popen(cmd, cwd=pb.get("cwd"), stdout=PIPE, stderr=PIPE, text=True, bufsize=1)
    _pump_progress(proc, pb["total_dur"], proxy, tmp)
    with _LOCK:
        if _STATE["state"] == "error":
            return False
    write_leveled_meta(Path(pb["meta"]), Path(pb["src"]), pb["angle"])
    return True


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

    # 並行排空 stderr：ffmpeg 的 stderr 是 PIPE，若不即時讀走，長片（尤其 4K）累積的
    # warning 會塞滿 ~64KB pipe 緩衝 → ffmpeg 阻塞在 stderr 寫入 → 不再吐 -progress →
    # watchdog 誤判卡死把健康渲染砍掉（曾踩雷：已寫 ~957MB 仍被誤殺）。留尾 50 行給錯誤訊息。
    stderr_lines: deque[str] = deque(maxlen=50)

    def _drain_stderr() -> None:
        try:
            assert proc.stderr is not None
            for sline in proc.stderr:
                stderr_lines.append(sline)
        except Exception:
            pass

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

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

    returncode = proc.wait()
    stderr_thread.join(timeout=2.0)
    stderr_tail = "".join(stderr_lines)
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
