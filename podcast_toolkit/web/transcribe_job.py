"""背景跑 Grok STT pipeline + resegment，餵狀態給前端 poll。

模組層級單一 job slot：同時間只能跑一個轉字幕。
前端流程：POST /api/transcribe → start_job() → 每秒 GET /api/transcribe/status。

Phase 順序：compress → upload → resegment → done
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any

from podcast_toolkit.episode import Episode
from podcast_toolkit.web import transcribe as _transcribe


def _backup_existing_per_mic_outputs(ep: Episode) -> list[str]:
    """重跑分軌前把 _final_v2.srt + .speakers.json 備份成時間戳檔，避免覆蓋手動編輯版本。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backed_up: list[str] = []
    for src in (ep.output_v2_srt(), ep.output_v2_speakers_json()):
        if not src.exists():
            continue
        dst = src.with_name(f"{src.stem}.{stamp}.bak{src.suffix}")
        dst.write_bytes(src.read_bytes())
        backed_up.append(str(dst.relative_to(ep.dir)))
    return backed_up


def _backup_existing_srts(ep: Episode) -> list[str]:
    """重轉字幕前把現有 _v2.srt / main_srt 備份成 .<timestamp>.bak.srt，避免覆蓋丟掉原稿。
    回傳實際備份的相對路徑清單（前端可顯示）；不存在的檔案略過。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backed_up: list[str] = []
    for src in (ep.output_v2_srt(), ep.main_srt()):
        if not src.exists():
            continue
        # foo_v2.srt → foo_v2.20260610-143000.bak.srt
        dst = src.with_name(f"{src.stem}.{stamp}.bak{src.suffix}")
        dst.write_bytes(src.read_bytes())
        backed_up.append(str(dst.relative_to(ep.dir)))
    return backed_up


_LOCK = threading.Lock()
# 無進度更新超過這個秒數 → 視為卡死（Gemini 上傳大檔可能久，給寬鬆值）
STALL_TIMEOUT_S = 30 * 60
# job 世代：start 時 +1。卡死被 timeout 廢棄的舊 worker 之後甦醒，
# 寫入會因世代不符被丟棄，不會污染新 job 的狀態。
_CURRENT_JOB = 0
_HEARTBEAT = 0.0  # 最近一次 worker 更新狀態的 monotonic 時間
_STATE: dict[str, Any] = {
    "state": "idle",       # idle | running | done | error
    "mode": "single",      # single | per-mic
    "phase": None,         # single: None | compress | upload | resegment
                           # per-mic: None | per-mic-transcribe | srt-merge | resegment
    "percent": 0.0,        # 該 phase 內 0-100（per-mic 用 done軌/總軌 換算）
    "src_path": None,      # 來源檔（相對 ep.dir）
    "out_srt": None,       # _v2.srt 相對 ep.dir，成功才會塞
    "backups": None,       # 重轉前備份的舊 SRT 路徑（相對 ep.dir）
    "mics_progress": None, # per-mic only：{a: "vad", b: "gemini", c: "done"} 等
    "error": None,
    "started_at": None,
}


def get_status() -> dict[str, Any]:
    global _CURRENT_JOB
    with _LOCK:
        # watchdog：running 但太久沒有任何進度更新 → 視為卡死，翻成 error
        # 並廢掉舊 worker 的寫入權（世代 +1），讓使用者可以重新開 job。
        if (
            _STATE["state"] == "running"
            and _HEARTBEAT
            and monotonic() - _HEARTBEAT > STALL_TIMEOUT_S
        ):
            _CURRENT_JOB += 1
            _STATE.update(
                state="error",
                error=f"轉字幕逾時：超過 {STALL_TIMEOUT_S // 60} 分鐘沒有進度更新，已視為卡死",
            )
        return dict(_STATE)


def _reset_locked(**kwargs) -> None:
    _STATE.update({
        "state": "idle",
        "mode": "single",
        "phase": None,
        "percent": 0.0,
        "src_path": None,
        "out_srt": None,
        "backups": None,
        "mics_progress": None,
        "error": None,
        "started_at": None,
    })
    _STATE.update(kwargs)


def _reset(**kwargs) -> None:
    with _LOCK:
        _reset_locked(**kwargs)


def _set(**kwargs) -> None:
    with _LOCK:
        _STATE.update(kwargs)


def _set_job(job: int, **kwargs) -> None:
    """worker 專用寫入：job 世代不符（已被 timeout 廢棄或被新 job 取代）就丟棄。"""
    global _HEARTBEAT
    with _LOCK:
        if job != _CURRENT_JOB:
            return
        _HEARTBEAT = monotonic()
        _STATE.update(kwargs)


def start_job(
    ep: Episode,
    *,
    src_rel: str,
    provider: str,
    api_key: str,
    typo_entries: list[dict] | None = None,
    glossary: list[dict] | None = None,
) -> dict[str, Any]:
    """開新 job；src_rel 是相對 ep.dir 的檔案路徑。provider: "xai" | "gemini"。

    typo_entries：全域錯字字典（~/.podcast-toolkit/typo-dict.json）。
    glossary：本集專有名詞詞庫（episode.yaml + defaults.yaml 合併後 normalize）。
    """
    global _CURRENT_JOB, _HEARTBEAT
    src = (ep.dir / src_rel).resolve()
    # 防路徑跳脫
    src.relative_to(ep.dir)
    if not src.is_file():
        raise FileNotFoundError(f"找不到檔案：{src_rel}")

    # 檢查 + 佔住 slot 必須在同一把鎖內，否則兩個併發 start 都會通過檢查
    with _LOCK:
        if _STATE["state"] == "running":
            raise RuntimeError("已有轉字幕正在進行中")
        _CURRENT_JOB += 1
        job = _CURRENT_JOB
        _HEARTBEAT = monotonic()
        _reset_locked(
            state="running",
            phase="compress",
            percent=0.0,
            src_path=src_rel,
            started_at=monotonic(),
        )

    worker = threading.Thread(
        target=_run,
        args=(ep, src, provider, api_key, typo_entries, glossary, job),
        daemon=True,
    )
    worker.start()
    return {"src_path": src_rel}


def _run(
    ep: Episode,
    src: Path,
    provider: str,
    api_key: str,
    typo_entries: list[dict] | None,
    glossary: list[dict] | None,
    job: int,
) -> None:
    """背景 worker：跑 pipeline → resegment → done / error。"""
    def setj(**kwargs) -> None:
        _set_job(job, **kwargs)

    def progress(phase: str, percent: float) -> None:
        setj(phase=phase, percent=float(percent))

    # 重轉前把現有 SRT 備份成時間戳檔（不覆蓋原本）
    try:
        backed = _backup_existing_srts(ep)
        if backed:
            setj(backups=backed)
    except Exception as e:
        setj(state="error", error=f"備份原 SRT 失敗：{e}")
        return

    try:
        _transcribe.run_pipeline(
            provider=provider,
            api_key=api_key,
            src_audio=src,
            out_srt=ep.main_srt(),
            work_dir=ep.subdir("work"),
            progress=progress,
            typo_entries=typo_entries,
            glossary=glossary,
        )
    except _transcribe.TranscribeError as e:
        setj(state="error", error=str(e))
        return
    except Exception as e:  # 其他預期外狀況也記下，避免 thread 沉默
        setj(state="error", error=f"轉字幕失敗：{e}")
        return

    # resegment：把字層 → 句子層 _v2.srt
    setj(phase="resegment", percent=0.0)
    try:
        from podcast_toolkit import resegment
        rc = resegment.run(ep.dir, force=True)
    except Exception as e:
        setj(state="error", error=f"resegment 失敗：{e}")
        return

    if rc != 0:
        setj(state="error", error=f"resegment 失敗 (rc={rc})")
        return

    out_srt_rel = str(ep.output_v2_srt().relative_to(ep.dir))
    setj(state="done", phase="resegment", percent=100.0, out_srt=out_srt_rel)


def start_per_mic_job(
    ep: Episode,
    *,
    speakers: list[str],
    force: bool = True,
) -> dict[str, Any]:
    """開新分軌轉錄 job；speakers 是要跑的軌（episode.yaml.mics 的 key 子集）。

    force 預設 True 因為前端要重轉就是要覆寫；舊檔已備份。
    """
    global _CURRENT_JOB, _HEARTBEAT
    mics = ep.mic_paths()
    if not mics:
        raise RuntimeError("episode.yaml 沒設 mics — 無法分軌轉錄")
    unknown = [s for s in speakers if s not in mics]
    if unknown:
        raise RuntimeError(f"speakers 含未知軌 {unknown}，episode.yaml mics 只有 {sorted(mics)}")
    if not speakers:
        raise RuntimeError("speakers 不能是空清單")

    init_progress = {sp: "queued" for sp in sorted(speakers)}
    # 檢查 + 佔住 slot 必須在同一把鎖內，否則兩個併發 start 都會通過檢查
    with _LOCK:
        if _STATE["state"] == "running":
            raise RuntimeError("已有轉字幕正在進行中")
        _CURRENT_JOB += 1
        job = _CURRENT_JOB
        _HEARTBEAT = monotonic()
        _reset_locked(
            state="running",
            mode="per-mic",
            phase="per-mic-transcribe",
            percent=0.0,
            mics_progress=init_progress,
            started_at=monotonic(),
        )

    worker = threading.Thread(
        target=_run_per_mic,
        args=(ep, sorted(speakers), force, job),
        daemon=True,
    )
    worker.start()
    return {"speakers": sorted(speakers)}


def _run_per_mic(ep: Episode, speakers: list[str], force: bool, job: int) -> None:
    """背景 worker：分軌轉錄 → srt_merge → done / error。"""
    from podcast_toolkit import gemini_subtitle, srt_merge

    def setj(**kwargs) -> None:
        _set_job(job, **kwargs)

    try:
        backed = _backup_existing_per_mic_outputs(ep)
        if backed:
            setj(backups=backed)
    except Exception as e:
        setj(state="error", error=f"備份 _final_v2 失敗：{e}")
        return

    def on_mic_progress(speaker: str, phase: str) -> None:
        """從 gemini_subtitle._emit 進來：更新 mics_progress[speaker] = phase。"""
        global _HEARTBEAT
        with _LOCK:
            if job != _CURRENT_JOB:
                return
            _HEARTBEAT = monotonic()
            progress = dict(_STATE.get("mics_progress") or {})
            progress[speaker] = phase
            done_count = sum(1 for p in progress.values() if p in ("done", "skipped"))
            _STATE["mics_progress"] = progress
            if speakers:
                _STATE["percent"] = round(done_count / len(speakers) * 100.0, 1)

    try:
        gemini_subtitle.transcribe_per_mic(
            ep,
            speakers=speakers,
            force=force,
            parallel=True,
            progress=on_mic_progress,
        )
    except Exception as e:
        setj(state="error", error=f"分軌轉錄失敗：{e}")
        return

    # 合併三路 SRT → _final_v2.srt + .speakers.json
    setj(phase="srt-merge", percent=0.0)
    try:
        rc = srt_merge.run(ep, force=True)
    except Exception as e:
        setj(state="error", error=f"srt_merge 失敗：{e}")
        return
    if rc != 0:
        setj(state="error", error=f"srt_merge 失敗 (rc={rc})")
        return

    out_srt_rel = str(ep.output_v2_srt().relative_to(ep.dir))
    setj(state="done", phase="srt-merge", percent=100.0, out_srt=out_srt_rel)
