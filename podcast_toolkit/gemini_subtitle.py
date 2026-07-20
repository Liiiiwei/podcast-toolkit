"""podcast subtitle：用 Gemini API 把音檔轉成 SRT 字幕。

詞庫分兩層：
  - defaults.yaml 的 common_glossary（所有集共用）
  - episode.yaml 的 glossary（本集專屬：來賓、本集獨有品牌）
合併後注入 prompt 第二段，讓 Gemini 在轉錄時直接寫對專有名詞，
避免轉完再做 find-replace（fixes 仍可作為事後保險，由 resegment 套用）。

雛形範圍：
  - 單檔上傳（用 google-genai File API，支援長音檔）
  - 一次 prompt 完成轉錄 + 斷句 + 詞庫替換
  - 輸出 03_成品/{name}_final.srt，可直接給 resegment 處理

之後要補：分段、retry、quota 控管、進度條。
"""
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from podcast_toolkit.episode import Episode
from podcast_toolkit.fsutil import atomic_write_text
from podcast_toolkit.web.transcribe import TranscribeError


def _config_gemini_key() -> str:
    """讀全域設定 ~/.podcast-toolkit/config.json 的 gemini_api_key（GUI「設定」存這裡）。
    讓只在編輯器設過金鑰、沒設環境變數的使用者，CLI / GUI 的 subtitle 路徑也拿得到 key
    （proofread.py 已是 env or config，這裡對齊它，補上唯一只讀 env 的缺口）。"""
    import json
    p = Path.home() / ".podcast-toolkit" / "config.json"
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("gemini_api_key", "") or ""
    except Exception:
        return ""


# 標點清單：半形 + 全形 + 中英文常見句末符號 + 刪節號
# 後處理用：移除字幕文字中所有標點（Gemini 不一定服從 prompt，這裡兜底）
_PUNCT_PATTERN = re.compile(
    r"[,，.。!！?？;；:：、……/\\「」『』\"'()（）\[\]【】《》<>]+"
)


def format_glossary_lines(items: list) -> list[str]:
    """把詞庫渲染成 prompt 條列（單軌與分軌 prompt 共用，格式只此一份）。
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


def build_prompt(gemini_cfg: dict, glossary: list) -> str:
    """組裝 Gemini 轉字幕 prompt。

    結構：
      1. 任務說明
      2. 詞庫（auto 替換）
      3. 字幕格式規則
      4. 輸出格式（純 SRT）
    """
    punct = gemini_cfg.get("punctuation", "none")
    if punct == "none":
        punct_rule = (
            "嚴禁使用任何標點符號（含半形 , . ? ! ; : 全形 ，。？！；： 頓號 、 引號「」"
            "刪節號 … 等等），字幕純文字輸出"
        )
    elif punct == "half_width":
        punct_rule = "標點符號使用半形（, . ? !），中文字句之間不空格"
    else:
        punct_rule = "標點符號使用全形（，。？！）"
    ellipsis_rule = (
        "禁止使用刪節號（… 或 ...）"
        if not gemini_cfg.get("allow_ellipsis", False)
        else "允許使用刪節號"
    )
    max_chars = gemini_cfg.get("max_chars_per_line", 17)
    min_chars = gemini_cfg.get("min_chars_per_card", 0)
    max_lines = gemini_cfg.get("max_lines_per_card", 1)
    extra_rules = gemini_cfg.get("extra_rules") or []

    rules = [
        f"每張字幕卡最多 {max_chars} 個中文字",
        f"每張字幕卡最多 {max_lines} 行",
    ]
    if min_chars > 0:
        rules.append(
            f"每張字幕卡至少 {min_chars} 個中文字；除非整句是純反應詞（「對」「嗯」「哈哈」"
            f"獨立講出的回應），否則寧可合併也不要切成 1-{min_chars-1} 字的瑣碎短卡"
        )
    rules += [
        "斷句以語意完整為優先，避免半句結尾（不要結束在「但是」「然後」「就是」這類連接詞）",
        "移除明顯的口頭禪疊字（如「對對對對」收成「對對對」、「然後然後」收成「然後」），但保留說話節奏",
        punct_rule,
        ellipsis_rule,
    ] + list(extra_rules)
    rules_block = "\n".join(f"- {r}" for r in rules)

    glossary_block = _format_glossary_block(glossary)

    return f"""你是專業的繁體中文 podcast 字幕編輯。請把這段音檔轉成 SRT 字幕。

【專有名詞詞庫】（重要：轉錄時請直接套用，不要寫錯）
{glossary_block}

【字幕格式規則】
{rules_block}

【輸出格式】
直接輸出標準 SRT（不要包 ```srt 或任何說明文字），格式：
1
00:00:00,000 --> 00:00:03,200
第一張字幕內容

2
00:00:03,200 --> 00:00:06,800
第二張字幕內容

時間碼用 hh:mm:ss,SSS（毫秒三位數，逗號分隔），不要用全形冒號。
"""


def post_clean_srt(
    srt_text: str,
    audio_duration_sec: Optional[float] = None,
    strip_punctuation: bool = True,
) -> str:
    """轉錄後處理：移除標點 + 過濾超出音檔長度的幻覺 cue。

    為什麼後處理：Gemini 不一定服從 prompt 的「禁標點」規則（實測會殘留）；
    且 VAD gate 後的 wav 在尾段全靜音時，Gemini 偶爾會幻覺出延伸到幾倍音檔長度的 cue
    （實測 38 分鐘音檔幻覺到 3.7 小時）。這層把兩種症狀都收掉。

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


def _reraise_timeout(exc: Exception, stage: str) -> None:
    """若 exc 屬於逾時類例外，轉成中文 TranscribeError 重新丟出；否則靜默返回。"""
    timeout_types: tuple = (TimeoutError,)
    # 選擇性引入 google / requests 的逾時型別（套件未裝時不影響）
    try:
        from google.api_core.exceptions import DeadlineExceeded
        timeout_types = (*timeout_types, DeadlineExceeded)
    except ImportError:
        pass
    try:
        import requests
        timeout_types = (*timeout_types, requests.exceptions.Timeout)
    except ImportError:
        pass
    if isinstance(exc, timeout_types) or "timeout" in type(exc).__name__.lower():
        raise TranscribeError(
            f"Gemini 音檔{stage}逾時（大音檔請改用本地 Breeze 轉錄）"
        ) from exc


def transcribe(audio_path: Path, prompt: str, model: str) -> str:
    """呼叫 Gemini API 把音檔轉成 SRT 文字。

    用 File API 上傳，避免 inline 大小限制；長音檔（>1 小時）建議再切段。
    """
    try:
        from google import genai
    except ImportError:
        print("✗ 缺少 google-genai 套件，請先安裝：", file=sys.stderr)
        print("    pip3 install --user google-genai", file=sys.stderr)
        raise

    api_key = os.environ.get("GEMINI_API_KEY") or _config_gemini_key()
    if not api_key:
        raise RuntimeError(
            "缺少 GEMINI API key。\n"
            "  在編輯器「設定」貼上，或 export GEMINI_API_KEY='your-key-here'\n"
            "  申請：https://aistudio.google.com/apikey"
        )

    client = genai.Client(api_key=api_key)

    print(f"→ 上傳音檔到 Gemini File API：{audio_path.name}")
    # google-genai 會把 basename 塞進 X-Goog-Upload-File-Name HTTP header；
    # header value 走 httpx 的 ascii encode，basename 含 CJK（如 episode 名「我愛上班」）
    # 會 UnicodeEncodeError。改用 ASCII 暫存 symlink 上傳，原檔不動。
    link_dir: Optional[Path] = None
    upload_target = audio_path
    try:
        audio_path.name.encode("ascii")
    except UnicodeEncodeError:
        link_dir = Path(tempfile.mkdtemp(prefix="gemini_upload_"))
        upload_target = link_dir / f"upload{audio_path.suffix}"
        os.symlink(audio_path.resolve(), upload_target)
    try:
        try:
            uploaded = client.files.upload(file=str(upload_target))
        except Exception as exc:
            _reraise_timeout(exc, "上傳")
            raise
    finally:
        if link_dir is not None:
            try:
                upload_target.unlink(missing_ok=True)
                link_dir.rmdir()
            except OSError:
                pass

    print(f"→ 呼叫 {model} 轉字幕（依音檔長度約 1-5 分鐘）")
    try:
        response = client.models.generate_content(
            model=model,
            contents=[prompt, uploaded],
        )
    except Exception as exc:
        _reraise_timeout(exc, "推論")
        raise

    return response.text or ""


def run(episode_dir: Path, force: bool = False, dry_run: bool = False, per_mic: bool = False) -> int:
    ep = Episode(episode_dir)
    if per_mic:
        # per-mic 分軌已改為本地能量分講者（mic_diarize），不再走 Gemini 雲端。
        from podcast_toolkit import mic_diarize
        rc = mic_diarize.run(ep, force=force)
        if rc not in (0, 1):
            print(f"✗ 本地分軌失敗（rc={rc}）", file=sys.stderr)
        return rc

    cfg = ep.cfg
    gemini_cfg = cfg.get("gemini") or {}

    try:
        audio = ep.main_audio()
    except FileNotFoundError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 3

    out = ep.output_srt()
    if out.exists() and not force:
        print(f"✗ 字幕已存在：{out}", file=sys.stderr)
        print("  加 --force 覆寫", file=sys.stderr)
        return 1

    # cfg["glossary"] 已由 config.merge 完成 normalize + 通用/專屬合併
    glossary = cfg.get("glossary") or []
    prompt = build_prompt(gemini_cfg, glossary)

    auto_fix_count = sum(len(it.get("sounds_like") or []) for it in glossary)
    print(f"✓ 音檔：{audio}")
    print(f"✓ 詞庫：共 {len(glossary)} 條（其中 {auto_fix_count} 個 sounds_like 已自動展開為 resegment fixes）")
    print(f"✓ 模型：{gemini_cfg.get('model', 'gemini-2.5-flash')}")

    if dry_run:
        print("\n--- prompt 預覽（--dry-run）---")
        print(prompt)
        print("--- end ---")
        return 0

    try:
        srt_text = transcribe(audio, prompt, gemini_cfg.get("model", "gemini-2.5-flash"))
    except Exception as e:
        print(f"✗ Gemini 轉錄失敗：{e}", file=sys.stderr)
        return 4

    if not srt_text.strip():
        print("✗ Gemini 回傳空字幕", file=sys.stderr)
        return 4

    atomic_write_text(out, srt_text)
    print(f"✓ 輸出：{out}")
    print(f"  下一步：podcast resegment \"{episode_dir}\"")
    return 0
