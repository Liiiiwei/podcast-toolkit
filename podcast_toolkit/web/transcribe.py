"""STT 轉字幕 pipeline：ffmpeg 壓縮 → xAI / Gemini STT → OpenCC s2tw → 寫 SRT。

呼叫方：web/api.py 的 POST /api/transcribe → transcribe_job.start_job → run_pipeline。
單一同步函式：失敗丟 TranscribeError，成功回寫到的 SRT 路徑。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import requests

from podcast_toolkit import srt_io


GROK_STT_URL = "https://api.x.ai/v1/stt"
# Gemini：用 google-genai SDK（Files API + generateContent）
GEMINI_MODEL = "gemini-2.5-flash"
# OpenAI Whisper-1：/v1/audio/transcriptions verbose_json + word timestamps；
# prompt 欄接受 224 token 的詞庫提詞偏值（_build_whisper_prompt 串成頓號分隔字串）
OPENAI_MODEL = "whisper-1"
# 上傳前壓縮成 16kHz mp3，足夠 STT 用，能大幅縮短上傳時間
COMPRESS_SAMPLE_RATE = "16000"
COMPRESS_BITRATE = "64k"
# 多單一 words[] 文字最長字數，超過硬切（Grok 偶爾回一整段 80+ 字）
SRT_MAX_CHARS = 30


class TranscribeError(RuntimeError):
    """轉字幕流程任一階段失敗都丟這個。"""


def _run_cloud_stt_pipeline(
    *,
    transcribe_fn,
    compressed_name: str,
    empty_msg: str,
    src_audio: Path,
    out_srt: Path,
    work_dir: Path,
    progress=None,
    typo_entries: list[dict] | None = None,
    post_words=None,
) -> Path:
    """雲端 STT pipeline 共用骨架：壓縮 → transcribe_fn → s2tw/錯字 → 寫 SRT。

    grok / gemini / openai 三家共用；provider 差異收斂在兩個 hook：
    - transcribe_fn(compressed: Path) -> list[dict]：回 words（{text,start,end}）
    - post_words(words) -> words：provider 特有的時間修正（mod60 / dedup），可省略

    呼叫端負責 fail-fast 的 SDK import 檢查（要在壓縮前就擋下來）。
    """
    if not shutil.which("ffmpeg"):
        raise TranscribeError("找不到 ffmpeg。請先 `brew install ffmpeg`。")

    work_dir.mkdir(parents=True, exist_ok=True)
    out_srt.parent.mkdir(parents=True, exist_ok=True)

    if progress:
        progress("compress", 0.0)
    compressed = work_dir / compressed_name
    _ffmpeg_compress(src_audio, compressed)
    if progress:
        progress("compress", 100.0)

    if progress:
        progress("upload", 0.0)
    words = transcribe_fn(compressed)
    if progress:
        progress("upload", 100.0)

    if not words:
        raise TranscribeError(empty_msg)
    words = [_convert_word(w, typo_entries=typo_entries) for w in words]
    if post_words is not None:
        words = post_words(words)
    cards = _words_to_cards(words)
    out_srt.write_text(srt_io.serialize(cards), encoding="utf-8")
    return out_srt


def run_grok_pipeline(
    *,
    api_key: str,
    src_audio: Path,
    out_srt: Path,
    work_dir: Path,
    progress=None,
    typo_entries: list[dict] | None = None,
    glossary: list[dict] | None = None,  # 統一介面接受；Grok STT 無 prompt 故僅靠 typo_entries 兜底
) -> Path:
    """完整 pipeline：壓縮 → 上傳 → 簡轉繁 → 套錯字字典 → 寫 SRT。

    src_audio: 集資料夾內任一可轉字幕檔案（mp3/wav/mp4/...）
    out_srt:   最終輸出位置（通常是 ep.output_v2_srt()）
    work_dir:  04_工作檔/，存壓縮後的暫存 mp3
    progress:  callable(phase: str, percent: float)，可選；用來餵 background job 狀態
    typo_entries: 使用者錯字字典 [{wrong, right, note}]；None 或空 list 都跳過。
                  glossary 的 sounds_like→canonical 也由 run_pipeline 展開塞進這個 list。
    回傳：out_srt 路徑
    """
    del glossary  # 訊號明確：Grok pipeline 不用 prompt-injected glossary
    return _run_cloud_stt_pipeline(
        transcribe_fn=lambda compressed: (_post_to_grok(api_key, compressed).get("words") or []),
        compressed_name=f"_grok_stt_{src_audio.stem}.mp3",
        empty_msg="Grok 回傳沒有 words，無法產生字幕",
        src_audio=src_audio,
        out_srt=out_srt,
        work_dir=work_dir,
        progress=progress,
        typo_entries=typo_entries,
    )


def run_gemini_pipeline(
    *,
    api_key: str,
    src_audio: Path,
    out_srt: Path,
    work_dir: Path,
    progress=None,
    typo_entries: list[dict] | None = None,
    glossary: list[dict] | None = None,
) -> Path:
    """Gemini STT pipeline：壓縮 → Files API 上傳 → generateContent → s2tw → 寫 SRT。

    typo_entries：使用者錯字字典（全域），塞進 prompt + _convert_word 兜底。
    glossary：本集專有名詞詞庫（episode-level），同樣塞進 prompt；
              sounds_like→canonical 由 run_pipeline 統一展開到 typo_entries。
    """
    # SDK import 在壓縮前就檢查，缺套件不要等壓完才失敗
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as e:
        raise TranscribeError(
            "缺少 google-genai；請跑 `pip3 install --user google-genai`"
        ) from e

    def _gemini_transcribe(compressed: Path) -> list[dict]:
        try:
            client = genai.Client(api_key=api_key)
            uploaded = client.files.upload(file=str(compressed))
            # Files API 上傳後可能需要等狀態變 ACTIVE
            for _ in range(60):
                state = getattr(uploaded.state, "name", str(uploaded.state))
                if state == "ACTIVE":
                    break
                if state == "FAILED":
                    raise TranscribeError(f"Gemini 檔案處理失敗：{uploaded.name}")
                time.sleep(1)
                uploaded = client.files.get(name=uploaded.name)
            prompt = build_gemini_prompt(typo_entries, glossary=glossary)
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[uploaded, prompt],
                # max_output_tokens 拉到 Flash 上限 65536；不設的話預設 8192，長集 STT
                # 字幕陣列會被截斷 → json.loads 壞在尾段（症狀：頭看似正常的 JSON）
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=65536,
                ),
            )
            text = (resp.text or "").strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                # 同時顯示頭尾 + 長度 + decode 位置，方便診斷截斷 vs 格式錯誤
                head = text[:200]
                tail = text[-200:] if len(text) > 200 else ""
                raise TranscribeError(
                    f"Gemini 回應不是 JSON（長度={len(text)}, decode_pos={e.pos}, "
                    f"err={e.msg}）\n頭 200 字：{head}\n尾 200 字：{tail}"
                ) from e
        except TranscribeError:
            raise
        except Exception as e:
            raise TranscribeError(f"Gemini STT 失敗：{e}") from e

    def _gemini_post_words(words: list[dict]) -> list[dict]:
        # Gemini 2.5 Flash 偶爾把 start/end 用 mod 60 回傳（只剩秒分量），
        # 導致 SRT 內 segment 時間不單調遞增 → 預覽時右側字幕卡片亂跳。
        # 用單調遞增假設重建絕對秒。
        words = _unwrap_mod60_times(words)
        # Gemini 也常把一句話拆成多條 entry 但給相同 start/end → 預覽時
        # activeCardAt(t) 永遠只回第一條，後面的卡片不顯示。把同時段平均切分。
        return _dedup_overlapping_times(words)

    return _run_cloud_stt_pipeline(
        transcribe_fn=_gemini_transcribe,
        compressed_name=f"_gemini_stt_{src_audio.stem}.mp3",
        empty_msg="Gemini 沒回傳任何字幕",
        src_audio=src_audio,
        out_srt=out_srt,
        work_dir=work_dir,
        progress=progress,
        typo_entries=typo_entries,
        post_words=_gemini_post_words,
    )


def run_openai_pipeline(
    *,
    api_key: str,
    src_audio: Path,
    out_srt: Path,
    work_dir: Path,
    progress=None,
    typo_entries: list[dict] | None = None,
    glossary: list[dict] | None = None,
) -> Path:
    """OpenAI STT pipeline：壓縮 → /v1/audio/transcriptions（whisper-1 word timestamps）→ s2tw → 寫 SRT。

    用 whisper-1 而非 gpt-4o-audio-preview：後者是 chat audio I/O 模型（tier 限制 + 已從
    public aliases 移除→ 404）。whisper-1 是 OpenAI 官方 STT 端點、全帳號可用，且
    verbose_json + timestamp_granularities=["word"] 直接回傳逐字時間軸，符合 word→cards pipeline。
    glossary 透過 prompt 參數做 vocabulary biasing（whisper prompt 上限 224 tokens，
    只塞 canonical 列表 + 錯字字典 right 端，不放長篇指令）。
    """
    # SDK import 在壓縮前就檢查，缺套件不要等壓完才失敗
    try:
        from openai import OpenAI
    except ImportError as e:
        raise TranscribeError(
            "缺少 openai SDK；請跑 `pip3 install --user openai`"
        ) from e

    def _openai_transcribe(compressed: Path) -> list[dict]:
        try:
            client = OpenAI(api_key=api_key)
            whisper_prompt = _build_whisper_prompt(typo_entries, glossary)
            with compressed.open("rb") as fh:
                kwargs = dict(
                    model=OPENAI_MODEL,
                    file=fh,
                    response_format="verbose_json",
                    timestamp_granularities=["word"],
                    language="zh",
                )
                if whisper_prompt:
                    kwargs["prompt"] = whisper_prompt
                resp = client.audio.transcriptions.create(**kwargs)
            # verbose_json 回傳物件含 .words = [{word, start, end}, ...]（SDK 物件需 .model_dump）
            raw_words = getattr(resp, "words", None) or []
            words = []
            for w in raw_words:
                if hasattr(w, "model_dump"):
                    w = w.model_dump()
                elif not isinstance(w, dict):
                    w = {
                        "word": getattr(w, "word", ""),
                        "start": getattr(w, "start", 0.0),
                        "end": getattr(w, "end", 0.0),
                    }
                words.append({
                    "text": w.get("word") or w.get("text") or "",
                    "start": float(w.get("start", 0.0)),
                    "end": float(w.get("end", 0.0)),
                })
            return words
        except TranscribeError:
            raise
        except Exception as e:
            raise TranscribeError(f"OpenAI STT 失敗：{e}") from e

    return _run_cloud_stt_pipeline(
        transcribe_fn=_openai_transcribe,
        compressed_name=f"_openai_stt_{src_audio.stem}.mp3",
        empty_msg="OpenAI 沒回傳任何字幕",
        src_audio=src_audio,
        out_srt=out_srt,
        work_dir=work_dir,
        progress=progress,
        typo_entries=typo_entries,
        post_words=_dedup_overlapping_times,
    )


def _build_whisper_prompt(
    typo_entries: list[dict] | None,
    glossary: list[dict] | None,
) -> str:
    """組 whisper-1 prompt：vocabulary biasing hint（不是指令）。

    whisper prompt 是「期望出現的詞彙」，會 bias decoder 用這些寫法。224 token 上限，
    所以只塞 glossary canonical + typo_entries 的 right 端，頓號分隔。"""
    hints: list[str] = []
    if glossary:
        for it in glossary:
            if not isinstance(it, dict):
                continue
            c = it.get("canonical")
            if c:
                hints.append(str(c))
    if typo_entries:
        for e in typo_entries:
            if not isinstance(e, dict):
                continue
            r = e.get("right")
            if r:
                hints.append(str(r))
    seen = set()
    uniq = []
    for h in hints:
        if h and h not in seen:
            seen.add(h)
            uniq.append(h)
    return "、".join(uniq)


def run_whisper_mlx_pipeline(
    *,
    api_key: str,  # 介面相容用，本地 provider 不需要 key
    src_audio: Path,
    out_srt: Path,
    work_dir: Path,
    progress=None,
    typo_entries: list[dict] | None = None,
    glossary: list[dict] | None = None,
) -> Path:
    """本地 mlx_whisper（Apple Silicon）+ VAD trim-and-stitch pipeline。

    不打雲端 API、不需要 key。流程：
    1. ffmpeg 解碼成 16kHz mono PCM
    2. VAD 找 speech segments（避開 30s window 級 drift）
    3. 每段獨立餵 mlx-community/whisper-large-v3-turbo
    4. segment-local 時間軸還原回全局時間軸
    5. 過濾 char-loop / phrase / 非中文幻覺 + s2twp 簡轉繁 + glossary 兜底

    glossary canonical 串成 initial_prompt 給 Whisper 做 vocabulary biasing。
    typo_entries 走文字後處理（_convert_word）。
    """
    del api_key  # 介面相容
    if not shutil.which("ffmpeg"):
        raise TranscribeError("找不到 ffmpeg。請先 `brew install ffmpeg`。")
    try:
        import numpy as np
    except ImportError as e:
        raise TranscribeError("缺少 numpy；請跑 `pip3 install --user numpy`") from e
    try:
        import mlx_whisper  # noqa: F401
    except ImportError as e:
        raise TranscribeError(
            "缺少 mlx-whisper；請跑 `pip3 install --user mlx-whisper`"
        ) from e
    try:
        from podcast_toolkit.vad_gate import (
            INT16_MAX,
            VAD_SAMPLE_RATE,
            _read_pcm_mono,
            apply_min_duration,
            apply_pad,
            detect_speech_frames,
        )
    except ImportError as e:
        raise TranscribeError(f"載入 vad_gate 失敗：{e}") from e

    work_dir.mkdir(parents=True, exist_ok=True)
    out_srt.parent.mkdir(parents=True, exist_ok=True)

    # 1. 讀 PCM（_read_pcm_mono 內部用 ffmpeg 解碼到 16kHz mono int16）
    if progress:
        progress("decode", 0.0)
    samples = _read_pcm_mono(src_audio, VAD_SAMPLE_RATE)
    if progress:
        progress("decode", 100.0)

    # 2. VAD 找 speech segments + 切過長段（>25s 強制切半，避開 Whisper 30s window）
    if progress:
        progress("vad", 0.0)
    frame_ms = 20
    frame_samples = int(VAD_SAMPLE_RATE * frame_ms / 1000)
    threshold = 0.02
    min_speech_sec = 0.3
    pad_sec = 0.15
    max_segment_sec = 25.0

    speech_mask = detect_speech_frames(
        samples, VAD_SAMPLE_RATE, frame_ms=frame_ms, threshold=threshold
    )
    speech_mask = apply_min_duration(
        speech_mask,
        frame_samples=frame_samples,
        sample_rate=VAD_SAMPLE_RATE,
        min_speech_sec=min_speech_sec,
    )
    speech_mask = apply_pad(
        speech_mask,
        frame_samples=frame_samples,
        sample_rate=VAD_SAMPLE_RATE,
        pad_sec=pad_sec,
    )
    padded = np.concatenate([[False], speech_mask, [False]])
    diff = np.diff(padded.astype(np.int8))
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0]
    segs: list[tuple[float, float]] = []
    for s, e in zip(starts, ends):
        s_sec = float(s * frame_samples / VAD_SAMPLE_RATE)
        e_sec = float(e * frame_samples / VAD_SAMPLE_RATE)
        if e_sec - s_sec <= max_segment_sec:
            segs.append((s_sec, e_sec))
        else:
            cur = s_sec
            while cur < e_sec:
                segs.append((cur, min(cur + max_segment_sec, e_sec)))
                cur += max_segment_sec
    if progress:
        progress("vad", 100.0)
    if not segs:
        raise TranscribeError("VAD 找不到任何講話段，無法轉字幕")

    # 3. 逐段 mlx_whisper
    import mlx_whisper

    model_path = "mlx-community/whisper-large-v3-turbo"
    initial_prompt_parts = ["以下是繁體中文 podcast 對話"]
    glossary_hint = _build_whisper_prompt(typo_entries, glossary)
    if glossary_hint:
        initial_prompt_parts.append(glossary_hint)
    initial_prompt = "、".join(initial_prompt_parts)

    if progress:
        progress("stt", 0.0)
    # warm-up：避免第一段卡很久
    warmup = np.zeros(int(VAD_SAMPLE_RATE * 0.5), dtype=np.float32)
    try:
        _ = mlx_whisper.transcribe(
            warmup, path_or_hf_repo=model_path, language="zh", verbose=None
        )
    except Exception as e:
        raise TranscribeError(f"載入 mlx_whisper 模型失敗：{e}") from e

    raw_cues: list[dict] = []
    for i, (s_sec, e_sec) in enumerate(segs):
        s_idx = int(s_sec * VAD_SAMPLE_RATE)
        e_idx = int(e_sec * VAD_SAMPLE_RATE)
        seg_pcm = samples[s_idx:e_idx].astype(np.float32) / INT16_MAX
        if len(seg_pcm) < int(VAD_SAMPLE_RATE * 0.2):
            continue
        try:
            result = mlx_whisper.transcribe(
                seg_pcm,
                path_or_hf_repo=model_path,
                language="zh",
                word_timestamps=False,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                initial_prompt=initial_prompt,
                verbose=None,
            )
        except Exception:
            continue
        for seg in result.get("segments", []):
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            g_start = s_sec + float(seg["start"])
            g_end = min(s_sec + float(seg["end"]), e_sec)
            if g_end <= g_start:
                continue
            raw_cues.append({"text": text, "start": g_start, "end": g_end})
        if progress and (i + 1) % 5 == 0:
            progress("stt", (i + 1) / len(segs) * 100)
    if progress:
        progress("stt", 100.0)

    if not raw_cues:
        raise TranscribeError("mlx_whisper 沒回傳任何字幕")

    # 4. s2twp + 套錯字字典 + 過濾幻覺
    words = [_convert_word(c, typo_entries=typo_entries) for c in raw_cues]
    words = _filter_whisper_hallucinations(words)
    if not words:
        raise TranscribeError("過濾完所有幻覺後沒剩字幕；可能 VAD threshold 太鬆")
    words = _dedup_overlapping_times(words)
    cards = _words_to_cards(words)
    out_srt.write_text(srt_io.serialize(cards), encoding="utf-8")
    return out_srt


# Whisper 常見訓練資料污染（中國平台結語、字幕組署名）；命中即丟。
_WHISPER_HALLUCINATION_PHRASES = (
    "請不吝", "不吝點贊", "訂閱 轉發", "明鏡與點點", "明鏡欄目",
    "请不吝", "字幕由", "Amara.org",
    "詞曲 李宗盛", "詞曲 曲 李宗盛", "李宗盛詞曲",
    "字幕志愿者",
)
# CJK 統一表意文字 block；不含 CJK 又夾雜 Cyrillic = 100% 噪訊幻覺。
import re as _re  # noqa: E402

_CJK_RE = _re.compile(r"[一-鿿]")
_CYRILLIC_RE = _re.compile(r"[А-я]")


def _is_char_loop(text: str, min_len: int = 30, min_unique_ratio: float = 0.15) -> bool:
    s = _re.sub(r"\s+", "", text)
    return len(s) >= min_len and len(set(s)) / len(s) < min_unique_ratio


def _filter_whisper_hallucinations(words: list[dict]) -> list[dict]:
    """過濾 char-loop / 中國平台 phrase / 純非中文 / Cyrillic 殘留 + 同字 5s 內重複。"""
    out: list[dict] = []
    seen_last: dict[str, float] = {}
    for w in words:
        text = (w.get("text") or "").strip()
        if not text:
            continue
        if _is_char_loop(text):
            continue
        if any(p in text for p in _WHISPER_HALLUCINATION_PHRASES):
            continue
        if _CJK_RE.search(text) is None:
            continue
        if _CYRILLIC_RE.search(text) is not None:
            continue
        start = float(w.get("start", 0.0))
        prev = seen_last.get(text)
        if prev is not None and start - prev < 5.0:
            continue
        seen_last[text] = start
        out.append({**w, "text": text, "start": start})
    return out


# 供應商分流表：UI 切換靠這個（順序也是 UI 顯示順序）
PROVIDERS: dict[str, callable] = {
    "xai": run_grok_pipeline,
    "gemini": run_gemini_pipeline,
    "openai": run_openai_pipeline,
    "whisper_mlx": run_whisper_mlx_pipeline,
}


def run_pipeline(
    *,
    provider: str,
    api_key: str,
    src_audio: Path,
    out_srt: Path,
    work_dir: Path,
    progress=None,
    typo_entries: list[dict] | None = None,
    glossary: list[dict] | None = None,
) -> Path:
    """根據 provider 分流到對應 pipeline。未知 provider 丟 TranscribeError。

    glossary 兩用：
    - 整包丟給 gemini provider，在 prompt 注入「必須寫成 X」段落
    - sounds_like→canonical 展開成 typo_entries 形式 merge 進去，給 _convert_word 兜底
      （Grok STT 沒 prompt，全靠這個兜底）
    """
    fn = PROVIDERS.get(provider)
    if fn is None:
        raise TranscribeError(f"未知的 STT 供應商：{provider}")
    merged_typo = _merge_glossary_into_typo(typo_entries, glossary)
    return fn(
        api_key=api_key,
        src_audio=src_audio,
        out_srt=out_srt,
        work_dir=work_dir,
        progress=progress,
        typo_entries=merged_typo,
        glossary=glossary,
    )


def _merge_glossary_into_typo(
    typo_entries: list[dict] | None,
    glossary: list[dict] | None,
) -> list[dict]:
    """把 glossary 的 sounds_like→canonical 展開成 {wrong, right} 疊到 typo 尾端。
    使用者錯字優先（在前），glossary 兜底（在後）；同 wrong 重複時前者勝出。
    """
    out = list(typo_entries or [])
    seen_wrong = {e.get("wrong") for e in out if isinstance(e, dict)}
    for it in glossary or []:
        if not isinstance(it, dict):
            continue
        canonical = it.get("canonical")
        if not canonical:
            continue
        for sound in it.get("sounds_like") or []:
            if not sound or sound == canonical or sound in seen_wrong:
                continue
            out.append({"wrong": sound, "right": canonical, "note": "glossary"})
            seen_wrong.add(sound)
    return out


def _ffmpeg_compress(src: Path, dst: Path) -> None:
    """壓成 16kHz mono mp3。失敗丟 TranscribeError。

    已存在且不比 src 舊就跳過（換 provider / 重跑同 provider 不必重壓）。
    換母帶後 src.mtime 會比 dst.mtime 新，會強制重壓。
    """
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vn",
        "-ac", "1",
        "-ar", COMPRESS_SAMPLE_RATE,
        "-b:a", COMPRESS_BITRATE,
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-5:]
        raise TranscribeError(f"ffmpeg 壓縮失敗：{' / '.join(tail)}")


def _post_to_grok(api_key: str, audio: Path) -> dict:
    """POST 到 x.ai STT。注意：file 欄位要排在最後。"""
    # 用 with 包住 file handle；連線失敗時也保證關閉
    with audio.open("rb") as fh:
        files = [
            ("format", (None, "true")),
            ("language", (None, "zh")),
            ("file", (audio.name, fh, "audio/mpeg")),
        ]
        try:
            resp = requests.post(
                GROK_STT_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files=files,
                timeout=600,
            )
        except requests.RequestException as e:
            raise TranscribeError(f"連線 x.ai 失敗：{e}") from e

    if resp.status_code != 200:
        body = resp.text[:300]
        raise TranscribeError(f"x.ai 回 HTTP {resp.status_code}：{body}")

    try:
        return resp.json()
    except ValueError as e:
        raise TranscribeError(f"x.ai response 不是 JSON：{resp.text[:200]}") from e


def _convert_word(w: dict, *, typo_entries: list[dict] | None = None) -> dict:
    """套 OpenCC s2tw（簡 → 繁，台灣字形），再套使用者錯字字典兜底。"""
    text = w.get("text") or ""
    text = _s2tw(text)
    text = apply_typo_dict(text, typo_entries)
    return {
        "start": float(w.get("start", 0.0)),
        "end": float(w.get("end", 0.0)),
        "text": text,
    }


def apply_typo_dict(text: str, entries: list[dict] | None) -> str:
    """字面 str.replace 套錯字字典；缺欄位的 entry 略過。"""
    if not entries:
        return text
    for e in entries:
        if not isinstance(e, dict):
            continue
        wrong = e.get("wrong")
        right = e.get("right")
        if not wrong or not right:
            continue
        text = text.replace(str(wrong), str(right))
    return text


def build_gemini_prompt(
    typo_entries: list[dict] | None,
    *,
    glossary: list[dict] | None = None,
) -> str:
    """組 Gemini prompt：要求斷句 + 錯字修正 + 填充詞處理 + 英文保留 + JSON 結構。

    glossary：本集專屬詞庫（episode.yaml + defaults.yaml 合併後的 normalize 結果），
    結構為 [{canonical, sounds_like, note}]。獨立於全域 typo_entries 之外，
    優先順序最高（來賓姓名 / 本集獨有的品牌名）。
    """
    base = (
        "請把這段中文音訊轉成繁體中文字幕，做到以下六件事：\n"
        "1. 依語意斷句：每句 15-30 字為佳；不要把完整意思切成兩半。\n"
        "2. 修正同音 / 近音錯字：依上下文判斷正確用字（例如「在」vs「再」、"
        "「的」vs「得」vs「地」、「製作」vs「致勝」這類）。\n"
        "3. 移除填充詞：「嗯」「啊」「呃」「就是」「然後就是」這類無意義口頭禪，"
        "可以刪除；但口語感的「然後」「所以」如果承載語意請保留。\n"
        "4. 英文人名 / 品牌 / 技術名詞保留原拼寫：例如 Claude、ChatGPT、"
        "Python、Notion 等不要硬翻譯成中文。\n"
        "5. text 內**絕對不要使用任何標點符號**：句號、逗號、問號、驚嘆號、頓號、"
        "冒號、分號、引號、括號、破折號等中英文標點全部不要。"
        "**原本會放逗號或頓號的地方改放一個半形空格**——也就是子句之間 / 短停頓 / "
        "語氣轉折處用空格分隔；但一個子句內部的字詞要連在一起，不要每兩三個字就插空格。"
        "正確示範：「我們今天要聊一個重要主題 就是 AI 對工作的影響」"
        "（兩個空格分三個子句，每個子句內字詞連寫）。"
        "錯誤示範 1（內部過度空格）：「我們 今天 要聊 一個 重要 主題」。"
        "錯誤示範 2（完全沒空格、句子糊在一起）：「我們今天要聊一個重要主題就是AI對工作的影響」。\n"
        "6. 輸出純 JSON 陣列，每筆 {\"start\": 秒（float）, \"end\": 秒（float）, "
        "\"text\": 字串}。不要包含 markdown 反引號。\n"
        "重要：start/end 是「從音訊 0 秒起的累計絕對秒數」，必須單調遞增；"
        "請勿用 mm:ss 拆開或對 60 取餘數（例如第 62 秒要寫成 62.0，不是 2.0）。"
    )

    if glossary:
        lines = []
        for it in glossary:
            if not isinstance(it, dict):
                continue
            canonical = it.get("canonical")
            if not canonical:
                continue
            sounds = it.get("sounds_like") or []
            note = it.get("note") or ""
            line = f"- 必須寫成「{canonical}」"
            if sounds:
                line += "（聽到類似「" + "」「".join(sounds) + "」也都寫成此正確版）"
            if note:
                line += f" — {note}"
            lines.append(line)
        if lines:
            base += (
                "\n\n本集專有名詞詞庫（來賓姓名 / 品牌 / 術語，最優先套用）：\n"
                + "\n".join(lines)
            )

    if typo_entries:
        lines = []
        for e in typo_entries:
            if not isinstance(e, dict):
                continue
            wrong = e.get("wrong")
            right = e.get("right")
            if not wrong or not right:
                continue
            note = e.get("note", "")
            tail = f"（{note}）" if note else ""
            lines.append(f"- 「{wrong}」→「{right}」{tail}")
        if lines:
            base += "\n\n以下是使用者標註的常見錯字，請特別注意一律改正：\n"
            base += "\n".join(lines)
    return base


_OPENCC = None  # 延遲載入，第一次呼叫才實例化


def _s2tw(text: str) -> str:
    global _OPENCC
    if _OPENCC is None:
        try:
            from opencc import OpenCC
        except ImportError as e:
            raise TranscribeError(
                "缺少 opencc-python-reimplemented；請跑 `pip3 install --user opencc-python-reimplemented`"
            ) from e
        _OPENCC = OpenCC("s2tw")
    return _OPENCC.convert(text)


def _unwrap_mod60_times(words: list[dict]) -> list[dict]:
    """Gemini 2.5 Flash 常把 start/end 用「秒 mod 60」回傳（少了分鐘分量）。
    例：seg N end=58.764 → seg N+1 start=59.204 end=1.764（end wrap）
         → seg N+2 start=2.224 end=7.954（start wrap，實為 62.224）
    用單調遞增 + 60s 步進重建絕對秒；若 start 突然倒退 >30s，視為跨分鐘。
    """
    out = []
    offset = 0.0
    last_end_abs = 0.0
    for w in words:
        s = float(w["start"]) + offset
        e = float(w["end"]) + offset
        # start 比上次 end 倒退 >30s → 視為 wrap，補一個 60s
        while s + 30 < last_end_abs:
            offset += 60.0
            s += 60.0
            e += 60.0
        # end < start → 該 segment 的 end 在分鐘界線後 wrap，補 60s
        while e < s:
            e += 60.0
        out.append({**w, "start": s, "end": e})
        last_end_abs = e
    return out


def _dedup_overlapping_times(words: list[dict]) -> list[dict]:
    """相鄰 word 共用同一個 start/end（Gemini 把整段話拆成多條 entry 但給同時間）→
    activeCardAt 線性掃描永遠回傳第一個 → 後面卡片在預覽中被遮蔽。
    把連續同時段的 entries 平均切分時間，讓每張卡片有不重疊的播放窗。
    """
    if not words:
        return words
    out: list[dict] = []
    i = 0
    while i < len(words):
        s = float(words[i]["start"])
        e = float(words[i]["end"])
        j = i + 1
        while (
            j < len(words)
            and float(words[j]["start"]) == s
            and float(words[j]["end"]) == e
        ):
            j += 1
        n = j - i
        if n == 1 or e <= s:
            out.append(words[i])
        else:
            step = (e - s) / n
            for k in range(n):
                w = dict(words[i + k])
                w["start"] = s + step * k
                w["end"] = s + step * (k + 1)
                out.append(w)
        i = j
    return out


def _words_to_cards(words: list[dict]) -> list[dict]:
    """把 Grok words[]（其實是句子層）轉成 SRT cards。

    Grok 回的每筆 word 偶爾長達 80+ 字。超過 SRT_MAX_CHARS 就照逗號 / 句號硬切。
    """
    cards: list[dict] = []
    idx = 1
    for w in words:
        for chunk in _split_long(w["text"], SRT_MAX_CHARS):
            chunk = chunk.strip()
            if not chunk:
                continue
            cards.append({
                "idx": idx,
                "start": w["start"],
                "end": w["end"],
                "text": chunk,
            })
            idx += 1
    return cards


def _split_long(text: str, maxlen: int) -> list[str]:
    """超過 maxlen 就照中文標點切；標點不夠再硬切。"""
    if len(text) <= maxlen:
        return [text]
    breaks = "，。！？；,.!?;"
    out: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in breaks and len(buf) >= maxlen * 0.6:
            out.append(buf)
            buf = ""
        elif len(buf) >= maxlen:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out
