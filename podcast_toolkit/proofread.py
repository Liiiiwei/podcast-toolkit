"""字幕語意校對引擎。

給定字幕卡,依四條規則(同音/近音錯字、專名詞庫、子句空格、去填充詞)逐句校對,
回傳逐卡修正 {idx: 新文字};**只改文字,不動時間 / 卡數 / 順序**。

Provider(鏡像 web.transcribe 的 PROVIDERS 抽象):
- claude_code:shell 呼叫本地 ``claude -p``。用使用者已登入的 Claude Code,
  **不需在工具裡設任何 API key、不外聯第三方 API**。Apple/一般機器皆可,前提是裝了 claude CLI。
- gemini:google-genai SDK,需 GEMINI_API_KEY。給「沒有 Claude Code」的使用者沿用原本路線。
- off:跳過(維持純手動)。

provider="auto"(預設)解析順序:claude CLI 在 → claude_code;否則有 gemini key → gemini;
否則 None(跳過)。所以非 Claude Code 使用者完全不受影響。
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from podcast_toolkit import srt_io
from podcast_toolkit.gemini_subtitle import format_glossary_lines


class ProofreadError(RuntimeError):
    """校對流程的可預期錯誤(缺 CLI / 缺 key / 模型回非 JSON / 逾時)。"""


# 四條規則:跟手動校對時餵給 agent 的同一套。措辭刻意保守,降低模型「亂改」風險。
PROOFREAD_RULES = """# 校對規則(逐句都要做)
1. 修同音 / 近音錯字:依上下文把明顯聽錯的字改對(例:在 vs 再、的 vs 得 vs 地、實際 vs 司機)。
2. 專有名詞照詞庫固定用字(最優先,見下方詞庫;沒詞庫就略過此項)。
3. 子句之間 / 語氣轉折 / 短停頓處插入一個半形空格(子句內字詞不要亂加空格)。
4. 去掉句首 / 句尾無意義填充詞(嗯 / 啊 / 呃 / 哦 / 哎,單獨或黏在頭尾的);
   句中承載語氣的「然後 / 所以 / 對」若有意義就保留。
5. 台灣口語慣用字:依語境改成台灣書面用字 —— 蠻→滿(蠻好→滿好;但「野蠻 / 蠻荒 / 蠻橫」維持「蠻」)、
   句尾軟化語氣的「嗎」→嘛(若是真的疑問句則維持「嗎」)、句尾轉語氣的「了」→啦。
   只改明確的台灣慣用情形,拿不準就維持原樣。
6. 人稱代名詞依語境正規化(僅在語境明確時才改,否則一律維持原字、不要猜):
   - 已知 / 對話明確為女性的對象 → 她;
   - 明確指動物(寵物 / 牲畜 / 受訪主題動物)→ 牠;
   - 物 / 事 / 抽象 → 它;
   - 性別或對象不明 → 維持原字。
7. 口語唸出的數字寫成國字(三、兩、四、二十);
   但年份、型號、含單位的阿拉伯寫法(3C / 2.0 / 5G)維持原樣。

# 嚴格限制(務必遵守)
- 只改文字,不動時間 / 卡序。英文(IG / OK / YT 等)保留。
- 代名詞只在規則 6 的明確情形改;不確定一律保持原字,不要猜。
- 不確定就保守:寧可只加空格 / 去填充詞,也別亂猜成你不確定的字;不要改寫、重述或變更原意。
- 整句是亂碼或殘缺、無法乾淨修復的,保持原文、不要列入輸出。"""


def build_prompt(cards: list[dict], glossary: list, *, context: str = "") -> str:
    """組單一塊的校對 prompt。輸入是 idx<TAB>原文,要求只回傳 {idx: 修正} JSON。"""
    gloss = format_glossary_lines(glossary)
    gloss_block = ("# 本集專有名詞詞庫(最優先套用)\n" + "\n".join(gloss) + "\n\n") if gloss else ""
    ctx_block = f"# 節目背景\n{context.strip()}\n\n" if context.strip() else ""
    lines = "\n".join(f'{c["idx"]}\t{c["text"]}' for c in cards)
    return (
        "你是繁體中文 podcast 字幕的專業校對引擎,逐句把 STT 自動稿修成乾淨字幕。\n\n"
        f"{ctx_block}{PROOFREAD_RULES}\n\n"
        f"{gloss_block}"
        "# 輸入(每行格式:idx<TAB>原文)\n"
        f"{lines}\n\n"
        "# 輸出(嚴格)\n"
        "只輸出一個 JSON 物件:key 是「有修改」的卡片 idx(字串),value 是修正後文字。\n"
        "沒有修改的卡片不要列入。不要輸出任何說明文字、不要 markdown 圍欄、不要反問——只有純 JSON。\n"
        '例:{"3":"修正後的句子","7":"另一句"}'
    )


def _extract_json_object(text: str) -> dict:
    """從模型輸出抽出第一個完整 JSON 物件,容忍 ```json 圍欄與前後雜訊。"""
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    a, b = s.find("{"), s.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(s[a:b + 1])
        except json.JSONDecodeError as e:
            raise ProofreadError(f"模型輸出無法解析成 JSON:{e}") from e
    raise ProofreadError("模型輸出裡找不到 JSON 物件")


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _merge_corrections(dst: dict, raw: dict) -> None:
    for k, v in (raw or {}).items():
        try:
            dst[int(k)] = str(v)
        except (TypeError, ValueError):
            continue


def _claude_one_chunk(chunk, glossary, *, model, timeout, context) -> dict:
    """跑單塊 ``claude -p ... --output-format json``,回 {idx: 修正} dict。失敗丟 ProofreadError。"""
    prompt = build_prompt(chunk, glossary, context=context)
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if model:
        cmd += ["--model", str(model)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ProofreadError(f"claude -p 逾時({timeout}s)") from e
    if proc.returncode != 0:
        raise ProofreadError(
            f"claude -p 失敗(rc={proc.returncode}):{proc.stderr.strip()[:300]}"
        )
    try:
        env = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ProofreadError(f"claude 回應不是 JSON 信封:{proc.stdout[:200]}") from e
    if env.get("is_error"):
        raise ProofreadError(f"claude 回報錯誤:{str(env.get('result', ''))[:300]}")
    return _extract_json_object(str(env.get("result", "")))


def _run_claude_code(cards, glossary, *, cfg, progress=None) -> dict:
    """本地 Claude Code:分塊 ``claude -p``,多塊**並行**跑。

    每塊大半時間在等模型回應(I/O bound),並行能把 1000+ 卡的牆鐘時間壓掉數倍。
    單塊失敗(逾時 / 非 JSON)不拖垮全部——記下來、回其餘已成功塊的修正(部分校對 > 零校對)。
    """
    if not shutil.which("claude"):
        raise ProofreadError(
            "找不到 claude CLI(本地 Claude Code)。請先安裝 Claude Code,"
            "或用 --provider gemini / 在設定改 proofread.provider。"
        )
    pcfg = cfg.get("proofread") or {}
    size = int(pcfg.get("chunk_size") or 150)
    model = pcfg.get("model")  # None → 用 Claude Code 預設模型
    timeout = int(pcfg.get("timeout_sec") or 300)
    context = pcfg.get("context") or ""
    workers = max(1, int(pcfg.get("max_workers") or 4))

    chunks = list(_chunks(cards, size))
    if not chunks:
        return {}

    out: dict[int, str] = {}
    failures: list[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as ex:
        futs = [
            ex.submit(_claude_one_chunk, ch, glossary,
                      model=model, timeout=timeout, context=context)
            for ch in chunks
        ]
        for fut in as_completed(futs):
            done += 1
            try:
                _merge_corrections(out, fut.result())
            except ProofreadError as e:
                failures.append(str(e))
            if progress:
                progress(done / len(chunks) * 100.0)

    if failures and not out:
        raise ProofreadError(f"校對全部 {len(chunks)} 塊都失敗:{failures[0]}")
    if failures:
        print(
            f"⚠ 校對 {len(failures)}/{len(chunks)} 塊失敗,已套用其餘成功塊:{failures[0][:150]}",
            file=sys.stderr,
        )
    return out


# 零雲端金鑰：Gemini 校對已移除，proofread 只走本地 claude -p（沒裝就跳過）。
PROVIDERS = {"claude_code": _run_claude_code}


def resolve_provider(cfg: dict) -> str | None:
    """決定實際 provider。回 None = 跳過校對(off / 找不到任何引擎)。"""
    pcfg = cfg.get("proofread") or {}
    p = (pcfg.get("provider") or "auto").lower()
    if p == "off":
        return None
    if p in PROVIDERS:
        return p
    # auto:本地 Claude Code（claude -p）；沒裝就跳過校對（零雲端金鑰，不走 Gemini）
    if shutil.which("claude"):
        return "claude_code"
    return None


def _norm(t: str) -> str:
    return t.replace(" ", "")


def qa_filter(cards_by_idx: dict, corrections: dict) -> tuple[dict, list]:
    """安全閘:套用前濾掉明顯捏造 / 錯位的修正(短卡被換成長句的特徵)。

    回傳 (applied, reverted)。reverted 的卡保持原文,列出來給人工複查。
    閾值刻意只擋「極端」:相似度極低 **且** 淨增很多字 → 幾乎一定是模型在亂塞;
    合理的大改(例:整句聽錯重修)相似度不會那麼低,不會被誤擋。
    """
    applied: dict[int, str] = {}
    reverted: list[tuple[int, str, str]] = []
    for idx, new in corrections.items():
        card = cards_by_idx.get(idx)
        if card is None:
            continue
        old = card["text"]
        if not new.strip() or new == old:
            continue
        ratio = difflib.SequenceMatcher(None, _norm(old), _norm(new)).ratio()
        delta = len(_norm(new)) - len(_norm(old))
        if ratio < 0.35 and delta >= 5:
            reverted.append((idx, old, new))
            continue
        applied[idx] = new
    return applied, reverted


def proofread_cards(cards, glossary, cfg, *, provider=None, progress=None):
    """純函式:對 cards 跑校對 + QA,不碰檔案。回傳 (provider, applied, reverted)。
    provider 回 None 表示跳過(off / 無引擎)。給 CLI 與未來 web job 共用。
    """
    prov = provider or resolve_provider(cfg)
    if prov is None:
        return None, {}, []
    if prov not in PROVIDERS:
        raise ProofreadError(f"未知的校對 provider:{prov}")
    raw = PROVIDERS[prov](cards, glossary, cfg=cfg, progress=progress)
    # 正規化 key 為 int(對齊 cards 的 idx;provider 應已是 int,防禦性再轉一次)
    corrections: dict[int, str] = {}
    for k, v in (raw or {}).items():
        try:
            corrections[int(k)] = str(v)
        except (TypeError, ValueError):
            continue
    by_idx = {c["idx"]: c for c in cards}
    applied, reverted = qa_filter(by_idx, corrections)
    return prov, applied, reverted


def run(episode_dir, *, provider=None, model=None, force=False, progress=None) -> int:
    """CLI 進入點:讀 _v2.srt → 校對 → 備份 → 寫回。回傳 exit code。

    model 覆寫 provider 的模型(claude_code 用 ``claude --model``;gemini 用 model id);
    None → 用 episode.yaml / defaults.yaml 的 proofread.model(再 None → provider 預設)。
    """
    from podcast_toolkit.episode import Episode

    ep = Episode(Path(episode_dir))
    cfg = ep.cfg
    v2 = ep.output_v2_srt()
    if not v2.exists():
        print(f"✗ 找不到字幕:{v2}", file=sys.stderr)
        print(f"  請先跑 podcast resegment / 轉字幕產生 {v2.name}", file=sys.stderr)
        return 3

    cards = srt_io.parse(v2.read_text(encoding="utf-8"))
    glossary = cfg.get("glossary") or []

    # 跳過已標刪除的卡:不浪費模型時間校對等下要砍掉的內容(大集省可觀)
    deletions = {int(d) for d in (cfg.get("deletions") or []) if str(d).strip().isdigit()}
    target_cards = [c for c in cards if c["idx"] not in deletions]
    if deletions:
        print(f"校對範圍:{len(target_cards)} 卡(跳過 {len(cards) - len(target_cards)} 張已刪卡)")

    pcfg_override = {
        **(cfg.get("proofread") or {}),
        **({"provider": provider} if provider else {}),
        **({"model": model} if model else {}),
    }
    try:
        prov, applied, reverted = proofread_cards(
            target_cards, glossary, {**cfg, "proofread": pcfg_override},
            progress=progress,
        )
    except ProofreadError as e:
        print(f"✗ 校對失敗:{e}", file=sys.stderr)
        return 1

    if prov is None:
        print("校對 provider = off(無 claude CLI 也無 Gemini key),已跳過。", file=sys.stderr)
        return 0

    if not applied and not reverted:
        print(f"校對({prov}):沒有需要修改的卡片。")
        return 0

    backup = v2.with_name(f"{v2.stem}.pre-proofread.bak{v2.suffix}")
    backup.write_text(v2.read_text(encoding="utf-8"), encoding="utf-8")
    for c in cards:
        if c["idx"] in applied:
            c["text"] = applied[c["idx"]]
    v2.write_text(srt_io.serialize(cards), encoding="utf-8")

    print(f"校對({prov}):修正 {len(applied)} 卡 / 安全閘還原可疑 {len(reverted)} 卡 / 共 {len(cards)} 卡")
    if reverted:
        print("  以下被還原成原文(疑似捏造,請人工複查):")
        for idx, old, new in reverted[:10]:
            print(f"    #{idx}「{old[:18]}」↛「{new[:18]}」")
    print(f"  備份:{backup.name}")
    return 0
