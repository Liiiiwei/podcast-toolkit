"""背景跑 Grok STT pipeline + resegment，餵狀態給前端 poll。

模組層級單一 job slot：同時間只能跑一個轉字幕。
前端流程：POST /api/transcribe → start_job() → 每秒 GET /api/transcribe/status。

Phase 順序：compress → upload → resegment → done
"""
from __future__ import annotations

import os
import re
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
# 模型載入期（job 開始後尚未解析到任何真實進度）的獨立 grace period：
# Breeze 載模型的前幾分鐘沒有 tqdm 輸出是正常的，不能用一般 stall 判準；
# 但真的 hang 在載入也要收得掉，所以給一個獨立（較短）的上限。
STARTUP_GRACE_S = 15 * 60
# 取消時等 worker 收尾的上限：Breeze 子行程被 terminate 後 worker 很快返回；
# 雲端 worker 卡在 HTTP 時 join 逾時就放生 —— 世代已 +1，殘餘寫入會被丟棄。
CANCEL_JOIN_TIMEOUT_S = 8.0
# job 世代：start 時 +1。卡死被 timeout 廢棄的舊 worker 之後甦醒，
# 寫入會因世代不符被丟棄，不會污染新 job 的狀態。
_CURRENT_JOB = 0
_HEARTBEAT = 0.0  # 最近一次 worker 更新狀態的 monotonic 時間
# 本世代 job 是否已回報過至少一筆真實進度（決定 watchdog 用 grace 還是 stall 判準）
_PROGRESS_SEEN = False
# 目前 job 的子行程（只有 Breeze 模式有）；只在 _LOCK 內讀寫，terminate/kill 在鎖外做
_ACTIVE_PROC: Any = None
# 目前 job 的 worker thread；cancel_job 用來等收尾。只在 _LOCK 內讀寫
_WORKER: threading.Thread | None = None
_STATE: dict[str, Any] = {
    "state": "idle",       # idle | running | done | error | cancelled
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
    global _CURRENT_JOB, _ACTIVE_PROC
    stale_proc = None
    with _LOCK:
        # watchdog：running 但太久沒有任何「真實進度」更新 → 視為卡死，翻成 error
        # 並廢掉舊 worker 的寫入權（世代 +1）＋終結子行程，讓使用者可以重新開 job。
        # 還沒解析到任何百分比（模型載入期）用獨立的 STARTUP_GRACE_S 判準，避免誤殺。
        limit = STALL_TIMEOUT_S if _PROGRESS_SEEN else STARTUP_GRACE_S
        if (
            _STATE["state"] == "running"
            and _HEARTBEAT
            and monotonic() - _HEARTBEAT > limit
        ):
            _CURRENT_JOB += 1
            stale_proc, _ACTIVE_PROC = _ACTIVE_PROC, None
            _STATE.update(
                state="error",
                error=f"轉字幕逾時：超過 {int(limit) // 60} 分鐘沒有進度更新，已視為卡死",
            )
        snap = dict(_STATE)
    # 子行程還活著就終結 —— 否則棄世代的舊行程會繼續寫 _v2.srt，跟下一個 job 互踩
    if stale_proc is not None and stale_proc.poll() is None:
        try:
            stale_proc.terminate()
        except OSError:
            pass
    return snap


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
    """worker 專用寫入：job 世代不符（已被 timeout 廢棄或被新 job 取代）就丟棄。

    心跳語意：這裡的每個既有呼叫點都對應「pipeline 真的往前走」（phase 轉換、
    解析到新百分比、終態）。禁止用無條件迴圈定期呼叫本函式 —— 那會把 watchdog
    完全架空（子行程活著但 hang 死時永遠不逾時）。
    """
    global _HEARTBEAT, _PROGRESS_SEEN
    with _LOCK:
        if job != _CURRENT_JOB:
            return
        _HEARTBEAT = monotonic()
        if kwargs.get("percent"):
            # 有非零百分比 = 已看過真實進度 → watchdog 改用一般 stall 判準
            _PROGRESS_SEEN = True
        _STATE.update(kwargs)


def _job_alive(job: int) -> bool:
    """worker 在 phase 邊界用：世代還有效才繼續往下走（被取消/棄世代就收手，
    不再進下一個會寫檔的階段，避免與新 job 互踩）。"""
    with _LOCK:
        return job == _CURRENT_JOB


def _grab_slot(**reset_kwargs) -> int:
    """搶單一 job slot（三種 start_* 共用）：檢查 running → 世代 +1 → 重置狀態。

    順便取出上一世代殘留的子行程（watchdog 翻 error 後可能還活著）在鎖外終結。
    回傳新 job 世代號；slot 被佔時 raise RuntimeError。
    """
    global _CURRENT_JOB, _HEARTBEAT, _PROGRESS_SEEN, _ACTIVE_PROC, _WORKER
    stale_proc = None
    with _LOCK:
        if _STATE["state"] == "running":
            raise RuntimeError("已有轉字幕正在進行中")
        _CURRENT_JOB += 1
        job = _CURRENT_JOB
        _HEARTBEAT = monotonic()
        _PROGRESS_SEEN = False
        stale_proc, _ACTIVE_PROC = _ACTIVE_PROC, None
        _WORKER = None
        _reset_locked(**reset_kwargs)
    if stale_proc is not None and stale_proc.poll() is None:
        try:
            stale_proc.terminate()
        except OSError:
            pass
    return job


def _spawn_worker(target, args) -> None:
    """啟動 worker thread 並記錄到 _WORKER（cancel_job 收尾時要 join 它）。"""
    global _WORKER
    worker = threading.Thread(target=target, args=args, daemon=True)
    with _LOCK:
        _WORKER = worker
    worker.start()


def _register_active_proc(job: int, proc) -> bool:
    """Breeze worker 把子行程掛上 slot。世代已不符（job 已被取消/棄世代）回 False，
    呼叫端要自己終結剛開出來的行程。"""
    global _ACTIVE_PROC
    with _LOCK:
        if job != _CURRENT_JOB:
            return False
        _ACTIVE_PROC = proc
        return True


def _clear_active_proc(proc) -> None:
    """子行程結束後解除掛載（只清掉自己那顆，避免蓋到新 job 的）。"""
    global _ACTIVE_PROC
    with _LOCK:
        if _ACTIVE_PROC is proc:
            _ACTIVE_PROC = None


def cancel_job() -> bool:
    """取消進行中的轉字幕：廢掉 worker 寫入權（世代 +1）→ 終結子行程 → 等 worker 收尾。

    回傳 True=有取消到、False=當下沒有 running 的 job。
    回傳時 state 已是 "cancelled"（slot 守衛只擋 running，故立即可重新開 job）。
    """
    global _CURRENT_JOB, _ACTIVE_PROC, _WORKER
    with _LOCK:
        if _STATE["state"] != "running":
            return False
        _CURRENT_JOB += 1  # 先廢寫入權：worker 之後的 setj 全被丟棄
        proc, _ACTIVE_PROC = _ACTIVE_PROC, None
        worker, _WORKER = _WORKER, None
        _STATE.update(state="cancelled", error=None)
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass
    # 等 worker 收尾（Breeze：proc 被砍 → wait 返回 → worker 很快結束；
    # 雲端：可能卡在 HTTP，join 逾時就放生，殘餘寫入已被世代擋掉）
    if worker is not None and worker.is_alive():
        worker.join(timeout=CANCEL_JOIN_TIMEOUT_S)
    # 保險：terminate 沒收掉（極少數卡 signal handler）就補 kill
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    return True


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
    src = (ep.dir / src_rel).resolve()
    # 防路徑跳脫
    src.relative_to(ep.dir)
    if not src.is_file():
        raise FileNotFoundError(f"找不到檔案：{src_rel}")

    # 檢查 + 佔住 slot 在 _grab_slot 的同一把鎖內完成，兩個併發 start 不會都通過
    job = _grab_slot(
        state="running",
        phase="compress",
        percent=0.0,
        src_path=src_rel,
        started_at=monotonic(),
    )
    _spawn_worker(_run, (ep, src, provider, api_key, typo_entries, glossary, job))
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

    # 被取消/棄世代 → 不進 resegment（會寫 _v2.srt，避免與新 job 互踩）
    if not _job_alive(job):
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
    mics = ep.mic_paths()
    if not mics:
        raise RuntimeError("episode.yaml 沒設 mics — 無法分軌轉錄")
    unknown = [s for s in speakers if s not in mics]
    if unknown:
        raise RuntimeError(f"speakers 含未知軌 {unknown}，episode.yaml mics 只有 {sorted(mics)}")
    if not speakers:
        raise RuntimeError("speakers 不能是空清單")

    init_progress = {sp: "queued" for sp in sorted(speakers)}
    # 檢查 + 佔住 slot 在 _grab_slot 的同一把鎖內完成，兩個併發 start 不會都通過
    job = _grab_slot(
        state="running",
        mode="per-mic",
        phase="per-mic-transcribe",
        percent=0.0,
        mics_progress=init_progress,
        started_at=monotonic(),
    )
    _spawn_worker(_run_per_mic, (ep, sorted(speakers), force, job))
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
        global _HEARTBEAT, _PROGRESS_SEEN
        with _LOCK:
            if job != _CURRENT_JOB:
                return
            _HEARTBEAT = monotonic()
            _PROGRESS_SEEN = True  # 分軌 phase 推進也算真實進度（維持 stall 判準）
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

    # 被取消/棄世代 → 不進 srt_merge（會寫 _final_v2.srt，避免與新 job 互踩）
    if not _job_alive(job):
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


# ── 一鍵 Breeze 轉字幕：Breeze ASR(make_subtitle.py) → ingest_breeze → 本地校對 ──

_TQDM_PCT_RE = re.compile(r"(\d{1,3})%\|")


def _parse_tqdm_percent(text: str) -> float | None:
    """從 whisper/tqdm 寫到 stderr 的進度行抽出百分比（0-100）。

    tqdm 預設格式的 l_bar 是「{percentage:3.0f}%|」，故只認「數字後緊接 |」的
    百分數，避免把句子裡的 '%'（如「使用率 55%」）誤判成進度。一段 buffer 內
    可能有多筆（\\r 刷新），取最後一筆＝最新進度。找不到回 None。
    """
    matches = _TQDM_PCT_RE.findall(text)
    if not matches:
        return None
    return max(0.0, min(100.0, float(matches[-1])))


def _pump_progress(stream, on_pct, tee=None) -> None:
    """即時讀子行程 stderr（bytes 串流），切出 tqdm 進度行並回報 %。

    tqdm 用 \\r（非 \\n）刷新，故不能 readline（會卡到最後才吐一坨）：讀 chunk 後
    切 [\\r\\n]，完整片段立刻解析，殘段（最新一次刷新，尚未被下個 \\r 收尾）也即時
    回報好讓 UI 追到最新。tee 若給則把原始文字附寫進去（保留錯誤診斷日誌）。
    此函式同時負責「排空」stderr，避免 pipe 塞滿讓 Breeze 卡死。
    """
    buf = ""
    while True:
        chunk = stream.read(256)
        if not chunk:
            break
        s = chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else chunk
        if tee is not None:
            tee.write(s)
            tee.flush()
        buf += s
        parts = re.split(r"[\r\n]", buf)
        buf = parts[-1]
        for seg in parts[:-1]:
            pct = _parse_tqdm_percent(seg)
            if pct is not None:
                on_pct(pct)
        live = _parse_tqdm_percent(buf)
        if live is not None:
            on_pct(live)
    tail = _parse_tqdm_percent(buf)
    if tail is not None:
        on_pct(tail)


def _breeze_dir() -> Path | None:
    """Breeze-ASR-25 專案路徑：config.json 的 breeze_dir 優先，否則試常見位置。
    認得的條件 = 該資料夾下有 make_subtitle.py。找不到回 None。"""
    import json

    from podcast_toolkit.config import toolkit_root

    cands: list[Path] = []
    cfg_path = Path.home() / ".podcast-toolkit" / "config.json"
    try:
        raw = (
            (json.loads(cfg_path.read_text(encoding="utf-8")) or {}).get("breeze_dir")
            if cfg_path.exists()
            else None
        )
    except (ValueError, OSError):
        raw = None
    if raw:
        cands.append(Path(str(raw)).expanduser())
    # 打包版(.app)：Breeze sidecar 內附在 Contents/Resources/breeze（frozen 時 toolkit_root()=Resources）。
    # 開發樹沒有這個資料夾 → is_file() 判斷自動略過，不影響本機開發流。
    cands.append(toolkit_root() / "breeze")
    cands.append(Path.home() / "Developer" / "breeze subtitle" / "Breeze-ASR-25")
    for c in cands:
        if (c / "make_subtitle.py").is_file():
            return c
    return None


def _breeze_python(bdir: Path) -> tuple[str, dict[str, str] | None]:
    """挑 Breeze 子進程要用的 python，並備妥 subprocess 環境變數。

    打包版(.app)：sidecar 內含自帶 py-runtime + site-packages（不能用 .venv——它的
    python 是指向本機 CLT 的 symlink，搬到別台 Mac 就斷）。回傳內附 framework python，
    並掛：
      PYTHONPATH      → site-packages（torch/whisper/numpy… 這包 794M ML 依賴）
      XDG_CACHE_HOME  → sidecar/cache（whisper.load_model 讀 <cache>/whisper/breeze-asr-25.pt，
                        SHA 相符即離線載入、不重新下載也不寫檔）
    其餘 os.environ 保留（含 launcher 已前插內附 ffmpeg 的 PATH——whisper 解碼音檔要 ffmpeg）。

    開發版：用 Breeze 專案自己的 .venv（沒有就退回系統 python3），env 不動（回 None＝Popen 繼承）。"""
    runtime_py = bdir / "py-runtime" / "bin" / "python3.9"
    site = bdir / "site-packages"
    if runtime_py.exists() and site.is_dir():
        env = dict(os.environ)
        env["PYTHONPATH"] = str(site)
        env["XDG_CACHE_HOME"] = str(bdir / "cache")
        # 別台 Mac 的 locale 未知（launchd 常給 C/POSIX）；強制 UTF-8 模式，
        # 免得 make_subtitle 印中文檔名時撞 ascii 編碼雷（PEP 540）。
        env["PYTHONUTF8"] = "1"
        return str(runtime_py), env
    venv_py = bdir / ".venv" / "bin" / "python"
    return (str(venv_py) if venv_py.exists() else "python3"), None


def start_breeze_job(ep: Episode, *, guest: str = "", terms: str = "") -> dict[str, Any]:
    """一鍵 Breeze：Breeze ASR 產含講者字幕 → ingest_breeze → 本地校對。整條龍背景跑。"""
    bdir = _breeze_dir()
    if bdir is None:
        raise RuntimeError(
            "找不到 Breeze 專案（make_subtitle.py）；請在 ~/.podcast-toolkit/config.json 設 breeze_dir 指向 Breeze-ASR-25 資料夾。"
        )
    # 檢查 + 佔住 slot 在 _grab_slot 的同一把鎖內完成，兩個併發 start 不會都通過
    job = _grab_slot(
        state="running", mode="breeze", phase="breeze-asr",
        percent=0.0, started_at=monotonic(),
    )
    _spawn_worker(_run_breeze, (ep, bdir, guest or "", terms or "", job))
    return {"ok": True}


def _run_breeze(ep: Episode, bdir: Path, guest: str, terms: str, job: int) -> None:
    """背景 worker：Breeze ASR → ingest_breeze → proofread → done/error。"""
    import subprocess

    from podcast_toolkit import ingest_breeze, proofread

    def setj(**kwargs) -> None:
        _set_job(job, **kwargs)

    # 1) Breeze ASR：make_subtitle.py（不帶 --quiet → whisper 把 tqdm 進度吐到
    #    stderr，pump 執行緒即時解析成 breeze-asr 這一 phase 的真實 %；自己找 Track*-Mic*.wav）
    py_str, run_env = _breeze_python(bdir)
    cmd = [
        py_str, str(bdir / "make_subtitle.py"),
        "--dir", str(ep.dir),
        "--guest", guest, "--terms", terms,
    ]
    errf = ep.subdir("work") / "_breeze_stderr.log"
    try:
        setj(phase="breeze-asr", percent=0.0)
        with open(errf, "w", encoding="utf-8") as ef:
            proc = subprocess.Popen(
                cmd, cwd=str(bdir), env=run_env,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, bufsize=0,
            )
            # 掛上 slot：watchdog 逾時或 cancel_job 才有辦法終結它。
            # 世代已不符（搶 slot 空窗被取消）→ 自己收掉剛開的行程並收工。
            if not _register_active_proc(job, proc):
                proc.terminate()
                proc.wait()
                return
            # pump：排空 stderr（避免 pipe 塞滿卡死）＋把 tqdm % 即時餵進狀態、tee 進日誌。
            # 心跳完全由 pump 解析到的真實進度驅動 —— 不做無條件輪詢心跳，
            # 否則子行程 hang 死（活著但無輸出）時 watchdog 永遠不會觸發。
            pump = threading.Thread(
                target=_pump_progress,
                args=(proc.stderr, lambda p: setj(phase="breeze-asr", percent=p), ef),
                daemon=True,
            )
            pump.start()
            proc.wait()
            pump.join(timeout=10)  # 收尾：讓最後一筆 % + 錯誤日誌寫完
        _clear_active_proc(proc)
        # 被取消/棄世代（proc 是被 terminate 收掉的）→ 直接收工，
        # 不進 ingest 等會寫檔的階段，避免與新 job 互踩
        if not _job_alive(job):
            return
        if proc.returncode != 0:
            tail = ""
            try:
                tail = errf.read_text(encoding="utf-8", errors="replace")[-600:]
            except OSError:
                pass
            setj(state="error", error=f"Breeze 轉錄失敗（rc={proc.returncode}）：{tail}")
            return
    except Exception as e:
        setj(state="error", error=f"Breeze 轉錄啟動失敗：{e}")
        return

    # 2) 匯入 Breeze 含講者 SRT → _v2.srt + speakers.json（去 [MicN]、MicN→speaker）
    setj(phase="ingest", percent=0.0)
    try:
        rc = ingest_breeze.run(ep.dir, force=True)
    except Exception as e:
        setj(state="error", error=f"匯入 Breeze 字幕失敗：{e}")
        return
    if rc != 0:
        setj(state="error", error="匯入 Breeze 字幕失敗：找不到含講者 SRT（Breeze 沒產出？）")
        return

    # ingest 期間被取消 → 不再進校對/重切（都會改寫 _v2.srt）
    if not _job_alive(job):
        return

    # 3) 本地校對（有引擎才跑；失敗不擋，字幕已匯入）
    try:
        if proofread.resolve_provider(ep.cfg):
            setj(phase="proofread", percent=0.0)
            proofread.run(ep.dir)
    except Exception:
        pass

    # 4) 依語句重切（要在 proofread 之後，需其加的空格當語句邊界；失敗不擋）
    try:
        from podcast_toolkit.subtitle_cleanup import reflow_episode
        reflow_episode(ep.dir)
    except Exception:
        pass

    out_srt_rel = str(ep.output_v2_srt().relative_to(ep.dir))
    setj(state="done", phase="proofread", percent=100.0, out_srt=out_srt_rel)
