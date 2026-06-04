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
# 上傳前壓縮成 16kHz mp3，足夠 STT 用，能大幅縮短上傳時間
COMPRESS_SAMPLE_RATE = "16000"
COMPRESS_BITRATE = "64k"
# 多單一 words[] 文字最長字數，超過硬切（Grok 偶爾回一整段 80+ 字）
SRT_MAX_CHARS = 30


class TranscribeError(RuntimeError):
    """轉字幕流程任一階段失敗都丟這個。"""


def run_grok_pipeline(
    *,
    api_key: str,
    src_audio: Path,
    out_srt: Path,
    work_dir: Path,
    progress=None,
    typo_entries: list[dict] | None = None,
) -> Path:
    """完整 pipeline：壓縮 → 上傳 → 簡轉繁 → 套錯字字典 → 寫 SRT。

    src_audio: 集資料夾內任一可轉字幕檔案（mp3/wav/mp4/...）
    out_srt:   最終輸出位置（通常是 ep.output_v2_srt()）
    work_dir:  04_工作檔/，存壓縮後的暫存 mp3
    progress:  callable(phase: str, percent: float)，可選；用來餵 background job 狀態
    typo_entries: 使用者錯字字典 [{wrong, right, note}]；None 或空 list 都跳過
    回傳：out_srt 路徑
    """
    if not shutil.which("ffmpeg"):
        raise TranscribeError("找不到 ffmpeg。請先 `brew install ffmpeg`。")

    work_dir.mkdir(parents=True, exist_ok=True)
    out_srt.parent.mkdir(parents=True, exist_ok=True)

    # 1. ffmpeg 壓縮成 16kHz mono mp3（暫存於 04_工作檔/）
    if progress:
        progress("compress", 0.0)
    compressed = work_dir / f"_grok_stt_{src_audio.stem}.mp3"
    _ffmpeg_compress(src_audio, compressed)
    if progress:
        progress("compress", 100.0)

    # 2. POST 到 x.ai
    if progress:
        progress("upload", 0.0)
    data = _post_to_grok(api_key, compressed)
    if progress:
        progress("upload", 100.0)

    # 3. 簡 → 繁 + 套錯字字典
    words = data.get("words") or []
    if not words:
        raise TranscribeError("Grok 回傳沒有 words，無法產生字幕")
    words = [_convert_word(w, typo_entries=typo_entries) for w in words]

    # 4. 寫 SRT
    cards = _words_to_cards(words)
    out_srt.write_text(srt_io.serialize(cards), encoding="utf-8")

    # 暫存檔可以保留方便除錯，需要時改成刪除
    return out_srt


def run_gemini_pipeline(
    *,
    api_key: str,
    src_audio: Path,
    out_srt: Path,
    work_dir: Path,
    progress=None,
    typo_entries: list[dict] | None = None,
) -> Path:
    """Gemini STT pipeline：壓縮 → Files API 上傳 → generateContent → s2tw → 寫 SRT。

    typo_entries：使用者錯字字典，會塞進 prompt 讓 Gemini 上下文判斷，
                  並在 _convert_word 兜底做 str.replace。
    """
    if not shutil.which("ffmpeg"):
        raise TranscribeError("找不到 ffmpeg。請先 `brew install ffmpeg`。")
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as e:
        raise TranscribeError(
            "缺少 google-genai；請跑 `pip3 install --user google-genai`"
        ) from e

    work_dir.mkdir(parents=True, exist_ok=True)
    out_srt.parent.mkdir(parents=True, exist_ok=True)

    if progress:
        progress("compress", 0.0)
    compressed = work_dir / f"_gemini_stt_{src_audio.stem}.mp3"
    _ffmpeg_compress(src_audio, compressed)
    if progress:
        progress("compress", 100.0)

    if progress:
        progress("upload", 0.0)
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
        prompt = build_gemini_prompt(typo_entries)
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[uploaded, prompt],
            config=genai_types.GenerateContentConfig(response_mime_type="application/json"),
        )
        text = (resp.text or "").strip()
        try:
            words = json.loads(text)
        except json.JSONDecodeError as e:
            raise TranscribeError(f"Gemini 回應不是 JSON：{text[:200]}") from e
    except TranscribeError:
        raise
    except Exception as e:
        raise TranscribeError(f"Gemini STT 失敗：{e}") from e
    if progress:
        progress("upload", 100.0)

    if not words:
        raise TranscribeError("Gemini 沒回傳任何字幕")
    words = [_convert_word(w, typo_entries=typo_entries) for w in words]
    cards = _words_to_cards(words)
    out_srt.write_text(srt_io.serialize(cards), encoding="utf-8")
    return out_srt


# 供應商分流表：UI 切換靠這個（順序也是 UI 顯示順序）
PROVIDERS: dict[str, callable] = {
    "xai": run_grok_pipeline,
    "gemini": run_gemini_pipeline,
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
) -> Path:
    """根據 provider 分流到對應 pipeline。未知 provider 丟 TranscribeError。"""
    fn = PROVIDERS.get(provider)
    if fn is None:
        raise TranscribeError(f"未知的 STT 供應商：{provider}")
    return fn(
        api_key=api_key,
        src_audio=src_audio,
        out_srt=out_srt,
        work_dir=work_dir,
        progress=progress,
        typo_entries=typo_entries,
    )


def _ffmpeg_compress(src: Path, dst: Path) -> None:
    """壓成 16kHz mono mp3。失敗丟 TranscribeError。"""
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


def build_gemini_prompt(typo_entries: list[dict] | None) -> str:
    """組 Gemini prompt：要求斷句 + 錯字修正 + 填充詞處理 + 英文保留 + JSON 結構。"""
    base = (
        "請把這段中文音訊轉成繁體中文字幕，做到以下五件事：\n"
        "1. 依語意斷句：每句 15-30 字為佳；不要把完整意思切成兩半。\n"
        "2. 修正同音 / 近音錯字：依上下文判斷正確用字（例如「在」vs「再」、"
        "「的」vs「得」vs「地」、「製作」vs「致勝」這類）。\n"
        "3. 移除填充詞：「嗯」「啊」「呃」「就是」「然後就是」這類無意義口頭禪，"
        "可以刪除；但口語感的「然後」「所以」如果承載語意請保留。\n"
        "4. 英文人名 / 品牌 / 技術名詞保留原拼寫：例如 Claude、ChatGPT、"
        "Python、Notion 等不要硬翻譯成中文。\n"
        "5. 輸出純 JSON 陣列，每筆 {\"start\": 秒（float）, \"end\": 秒（float）, "
        "\"text\": 字串}。不要包含 markdown 反引號。"
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
