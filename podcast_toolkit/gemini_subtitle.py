"""字幕共用 helper：詞庫渲染與 SRT 後處理。

原本此模組負責 Gemini 轉字幕，Gemini 路線已整條移除；只保留仍被
proofread / web.transcribe 依賴的共用工具：

  - format_glossary_lines / _format_glossary_block：把詞庫渲染成 prompt 條列
  - _PUNCT_PATTERN / post_clean_srt：字幕文字的標點清理與幻覺 cue 過濾

詞庫分兩層（defaults.yaml 的 common_glossary + episode.yaml 的 glossary），
合併後由呼叫端渲染注入。
"""
import re
from typing import Optional


# 標點清單：半形 + 全形 + 中英文常見句末符號 + 刪節號
# 後處理用：移除字幕文字中所有標點
_PUNCT_PATTERN = re.compile(
    r"[,，.。!！?？;；:：、……/\\「」『』\"'()（）\[\]【】《》<>]+"
)


def format_glossary_lines(items: list) -> list[str]:
    """把詞庫渲染成 prompt 條列（格式只此一份）。
    防禦性略過非 dict / 缺 canonical 的條目。"""
    lines: list[str] = []
    for it in items or []:
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
    return lines


def _format_glossary_block(items: list) -> str:
    """把詞庫渲染成 prompt 可讀的條列文字。"""
    lines = format_glossary_lines(items)
    if not lines:
        return "（本集無專有名詞詞庫）"
    return "\n".join(lines)


def post_clean_srt(
    srt_text: str,
    audio_duration_sec: Optional[float] = None,
    strip_punctuation: bool = True,
) -> str:
    """轉錄後處理：移除標點 + 過濾超出音檔長度的幻覺 cue。

    參數：
      audio_duration_sec: 給就過濾 start > 1.05 * duration 的 cue（留 5% 餘裕避免邊界誤殺）；
        None 則不過濾。
      strip_punctuation: 是否從每張卡文字移除所有標點符號（_PUNCT_PATTERN 涵蓋）。
    """
    from podcast_toolkit import srt_io

    cards = srt_io.parse(srt_text)
    if not cards:
        return srt_text

    cutoff = audio_duration_sec * 1.05 if audio_duration_sec else float("inf")
    out_lines: list[str] = []
    new_idx = 1
    dropped_hallucination = 0
    for c in cards:
        if c["start"] > cutoff:
            dropped_hallucination += 1
            continue
        text = c["text"]
        if strip_punctuation:
            text = _PUNCT_PATTERN.sub("", text)
        text = text.strip()
        if not text:
            continue
        start_ts = srt_io.seconds_to_srt_ts(c["start"])
        end_ts = srt_io.seconds_to_srt_ts(c["end"])
        out_lines.append(f"{new_idx}\n{start_ts} --> {end_ts}\n{text}\n")
        new_idx += 1

    if dropped_hallucination:
        print(
            f"  ⚠ post_clean 過濾 {dropped_hallucination} 張幻覺 cue（超過音檔 "
            f"{audio_duration_sec:.1f}s × 1.05 邊界）"
        )
    return "\n".join(out_lines)
