"""Grok STT 轉字幕 pipeline：ffmpeg 壓縮 → x.ai STT → OpenCC s2tw → 寫 SRT。

呼叫方：web/api.py 的 POST /api/transcribe。
單一同步函式：失敗丟 TranscribeError，成功回寫到的 SRT 路徑。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

from podcast_toolkit import srt_io


GROK_STT_URL = "https://api.x.ai/v1/stt"
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
) -> Path:
    """完整 pipeline：壓縮 → 上傳 → 簡轉繁 → 寫 SRT。

    src_audio: 集資料夾內任一可轉字幕檔案（mp3/wav/mp4/...）
    out_srt:   最終輸出位置（通常是 ep.output_v2_srt()）
    work_dir:  04_工作檔/，存壓縮後的暫存 mp3
    回傳：out_srt 路徑
    """
    if not shutil.which("ffmpeg"):
        raise TranscribeError("找不到 ffmpeg。請先 `brew install ffmpeg`。")

    work_dir.mkdir(parents=True, exist_ok=True)
    out_srt.parent.mkdir(parents=True, exist_ok=True)

    # 1. ffmpeg 壓縮成 16kHz mono mp3（暫存於 04_工作檔/）
    compressed = work_dir / f"_grok_stt_{src_audio.stem}.mp3"
    _ffmpeg_compress(src_audio, compressed)

    # 2. POST 到 x.ai
    data = _post_to_grok(api_key, compressed)

    # 3. 簡 → 繁（s2tw：台灣字形，但不做詞彙替換）
    words = data.get("words") or []
    if not words:
        raise TranscribeError("Grok 回傳沒有 words，無法產生字幕")
    words = [_convert_word(w) for w in words]

    # 4. 寫 SRT
    cards = _words_to_cards(words)
    out_srt.write_text(srt_io.serialize(cards), encoding="utf-8")

    # 暫存檔可以保留方便除錯，需要時改成刪除
    return out_srt


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


def _convert_word(w: dict) -> dict:
    """套 OpenCC s2tw（簡 → 繁，台灣字形；不做詞彙替換以保留原意）。"""
    text = w.get("text") or ""
    return {
        "start": float(w.get("start", 0.0)),
        "end": float(w.get("end", 0.0)),
        "text": _s2tw(text),
    }


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
