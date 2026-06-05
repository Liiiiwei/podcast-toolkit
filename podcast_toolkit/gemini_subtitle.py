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
import sys
from pathlib import Path
from typing import Optional

from podcast_toolkit.episode import Episode


def _format_glossary_block(items: list) -> str:
    """把詞庫渲染成 prompt 可讀的條列文字。"""
    if not items:
        return "（本集無專有名詞詞庫）"
    lines = []
    for it in items:
        canonical = it["canonical"]
        sounds = it.get("sounds_like") or []
        note = it.get("note") or ""
        line = f"- 必須寫成「{canonical}」"
        if sounds:
            line += "（聽到類似「" + "」「".join(sounds) + "」也都寫成此正確版）"
        if note:
            line += f" — {note}"
        lines.append(line)
    return "\n".join(lines)


def build_prompt(gemini_cfg: dict, glossary: list) -> str:
    """組裝 Gemini 轉字幕 prompt。

    結構：
      1. 任務說明
      2. 詞庫（auto 替換）
      3. 字幕格式規則
      4. 輸出格式（純 SRT）
    """
    punct = gemini_cfg.get("punctuation", "half_width")
    punct_rule = (
        "標點符號使用半形（, . ? !），中文字句之間不空格"
        if punct == "half_width"
        else "標點符號使用全形（，。？！）"
    )
    ellipsis_rule = (
        "禁止使用刪節號（… 或 ...）"
        if not gemini_cfg.get("allow_ellipsis", False)
        else "允許使用刪節號"
    )
    max_chars = gemini_cfg.get("max_chars_per_line", 17)
    max_lines = gemini_cfg.get("max_lines_per_card", 1)
    extra_rules = gemini_cfg.get("extra_rules") or []

    rules = [
        f"每張字幕卡最多 {max_chars} 個中文字",
        f"每張字幕卡最多 {max_lines} 行",
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

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "缺少 GEMINI_API_KEY 環境變數。\n"
            "  export GEMINI_API_KEY='your-key-here'\n"
            "  申請：https://aistudio.google.com/apikey"
        )

    client = genai.Client(api_key=api_key)

    print(f"→ 上傳音檔到 Gemini File API：{audio_path.name}")
    uploaded = client.files.upload(file=str(audio_path))

    print(f"→ 呼叫 {model} 轉字幕（依音檔長度約 1-5 分鐘）")
    response = client.models.generate_content(
        model=model,
        contents=[prompt, uploaded],
    )

    return response.text or ""


def run(episode_dir: Path, force: bool = False, dry_run: bool = False) -> int:
    ep = Episode(episode_dir)
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

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(srt_text, encoding="utf-8")
    print(f"✓ 輸出：{out}")
    print(f"  下一步：podcast resegment \"{episode_dir}\"")
    return 0
