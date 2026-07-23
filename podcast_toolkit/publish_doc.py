"""上架文案產生器。

把一集的「成品時間軸」逐字稿餵給本地 Claude Code（``claude -p``），產出 YouTube
上架用的整份文案：影片標題（推薦＋備選）、說明欄、章節時間軸、本集重點、社群短文，
再由本模組拼上確定性欄位（名詞對照、待補欄位、成品資訊），輸出成 03_成品/{name}_上架文案.txt。

分兩層，各司其職：
- 確定性（本模組算，保證與成品對齊）：
  * 章節時間軸走 assemble.prepare_assembly(subtitle_mode='sidecar')，時間戳＝成品時間軸
    （收刪段 → ÷倍速 → +片頭偏移），與實際出片同一套換算，不會對不齊。
  * 名詞對照 ← episode.yaml glossary 的 canonical（避免文案打錯專名）。
  * 待補欄位（EP 編號 / 連結 / 主持人）＝上架前人工確認清單。
- 編輯性（本地 Claude Code，鏡像 proofread 的 claude_code provider）：
  讀成品逐字稿 → 回 JSON（標題 / 說明欄 / 章節標題＋邊界 / 重點 / 社群短文）。
  用使用者已登入的 Claude Code，**不需 API key、不外聯第三方**。沒裝 claude → 明確報錯。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from podcast_toolkit import config, srt_io
from podcast_toolkit.episode import Episode


class PublishDocError(RuntimeError):
    """上架文案流程的可預期錯誤（缺 claude CLI / 模型回非 JSON / 逾時 / 缺成品）。"""


# ---- 成品時間軸逐字稿 ---------------------------------------------------------

def build_final_timeline_srt(episode_dir: Path) -> tuple[str, float]:
    """回（成品時間軸 SRT 字串, 成品總長秒數）——章節時間戳的唯一真實來源。

    直接重用合成管線的 sidecar 產物：prepare_assembly 算出來的 removed_intervals、
    倍速、片頭偏移都與實際出片一致，所以這裡拿到的時間戳貼上 YouTube 就會對得齊。
    只取字幕內容、不跑 ffmpeg。
    """
    from podcast_toolkit import assemble

    plan = assemble.prepare_assembly(
        episode_dir, output_kind="yt", force=True, subtitle_mode="sidecar",
    )
    sidecar = plan.get("sidecar_srt")
    if not sidecar or not sidecar.get("content"):
        raise PublishDocError("sidecar 字幕產生失敗（prepare_assembly 未回傳內容）")
    return sidecar["content"], float(plan.get("total_dur") or 0.0)


def _fmt_ts(sec: float) -> str:
    """秒 → YouTube 章節時間戳（未滿一小時 M:SS，滿一小時 H:MM:SS）。"""
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _transcript_for_prompt(srt_text: str) -> str:
    """把成品時間軸 SRT 壓成「M:SS 文字」逐行稿，餵給模型挑章節邊界。"""
    cards = srt_io.parse(srt_text)
    lines = []
    for c in cards:
        lines.append(f"{_fmt_ts(float(c['start']))} {str(c.get('text', '')).strip()}")
    return "\n".join(lines)


# ---- Claude Code 產文案 -------------------------------------------------------

def _show_context(ep: Episode) -> dict:
    """從 episode.yaml 的 publish 區塊（可選）帶入節目/主持人/來賓等固定資訊。

    沒設就留空，交給說明欄的待補欄位；不動 config.merge 白名單，直接讀 raw yaml。
    """
    import yaml

    y = ep.dir / "episode.yaml"
    raw = {}
    if y.exists():
        try:
            raw = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {}
    pub = raw.get("publish") or {}
    return {
        "show": pub.get("show") or "",
        "hosts": pub.get("hosts") or [],
        "guest": pub.get("guest") or "",
        "guest_intro": pub.get("guest_intro") or "",
        "links": pub.get("links") or [],
        "ep_number": pub.get("ep_number") or "",
    }


def build_prompt(transcript: str, ctx: dict) -> str:
    """組上架文案 prompt。要求模型只回一個 JSON 物件（結構見下）。"""
    show = ctx.get("show") or "（未提供節目名，說明欄請留待補）"
    hosts = "、".join(ctx.get("hosts") or []) or "（未提供）"
    guest = ctx.get("guest") or "（逐字稿中辨識）"
    guest_intro = ctx.get("guest_intro") or ""
    ctx_block = f"節目：{show}\n主持人：{hosts}\n來賓：{guest}"
    if guest_intro:
        ctx_block += f"\n來賓補充：{guest_intro}"

    return f"""你是中文 Podcast 影片的上架文案編輯。以下是一集節目「成品時間軸」的逐字稿，每行格式為「時間 內容」，時間就是最終影片的時間戳。

# 節目資訊
{ctx_block}

# 逐字稿（成品時間軸）
{transcript}

# 你的任務
產出一份 YouTube 上架文案，**只回傳一個 JSON 物件**（不要有 JSON 以外的任何文字、不要包 markdown code fence），結構如下：

{{
  "title_recommended": "最推薦的影片標題（含節目名，結尾可留 EP__ 佔位）",
  "title_alternatives": ["備選標題 1", "備選標題 2", "備選標題 3"],
  "description": "YouTube 說明欄整段內文（可換行；開頭一句鉤子 + 來賓與內容介紹 2-3 段 + 給觀眾的收穫），不要含章節與 hashtag，那些另外欄位處理",
  "chapters": [
    {{"time": "0:00", "title": "開場章節標題"}},
    {{"time": "M:SS", "title": "章節標題"}}
  ],
  "highlights": ["本集重點條列 1", "本集重點條列 2"],
  "social": "IG/FB 用的社群短文（3-5 行，結尾引導看完整集）",
  "hashtags": ["我愛上班", "其他標籤"]
}}

# 規則
- 全程繁體中文，不要出現日文、簡體字、或多餘英文對照（React/IG/YT 等專有名詞可保留）。
- 章節：第一個必須是 "0:00"；時間戳直接沿用逐字稿裡某一行的時間（挑該主題真正開始的那一行），不要自己編時間；章節之間至少間隔 10 秒；數量抓 12-20 個，覆蓋整集主題流。章節標題要精簡吸引人，別直接抄逐字稿原句。
- 說明欄語氣自然、對觀眾說話，凸顯來賓的獨特故事與觀眾能得到什麼。
- title_recommended 結尾若適合放集數，用 "EP__" 佔位讓人工補。
- 只根據逐字稿內容寫，不要杜撰逐字稿沒有的事實。"""


def _run_claude(prompt: str, *, model: str | None, timeout: int) -> dict:
    """跑 ``claude -p ... --output-format json``，回解析後的文案 dict。失敗丟 PublishDocError。"""
    if not shutil.which("claude"):
        raise PublishDocError(
            "找不到 claude CLI（本地 Claude Code）。請先安裝 Claude Code 再跑上架文案。"
        )
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if model:
        cmd += ["--model", str(model)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise PublishDocError(f"claude -p 逾時（{timeout}s）") from e
    if proc.returncode != 0:
        raise PublishDocError(
            f"claude -p 失敗（rc={proc.returncode}）：{proc.stderr.strip()[:300]}"
        )
    try:
        env = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise PublishDocError(f"claude 回應不是 JSON 信封：{proc.stdout[:200]}") from e
    if env.get("is_error"):
        raise PublishDocError(f"claude 回報錯誤：{str(env.get('result', ''))[:300]}")
    return _extract_json_object(str(env.get("result", "")))


def _extract_json_object(text: str) -> dict:
    """從模型回覆抽出第一個 JSON 物件（容忍前後有多餘文字或 code fence）。"""
    text = text.strip()
    # 去掉可能的 ```json fence
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退而求其次：抓第一個 {...} 平衡括號
    start = text.find("{")
    if start < 0:
        raise PublishDocError(f"模型回覆找不到 JSON 物件：{text[:200]}")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError as e:
                    raise PublishDocError(f"模型回覆 JSON 解析失敗：{e}") from e
    raise PublishDocError(f"模型回覆 JSON 括號不完整：{text[:200]}")


# ---- 章節後處理（保成品對齊） --------------------------------------------------

def _ts_to_sec(ts: str) -> int:
    """M:SS 或 H:MM:SS → 秒。壞格式回 -1。"""
    parts = str(ts).strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return -1
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return -1


def _normalize_chapters(chapters: list, total_dur: float) -> list[dict]:
    """清洗模型回的章節：第一個強制 0:00、時間單調遞增、去掉超出片長或間隔<10s 的。"""
    cleaned = []
    for ch in chapters or []:
        title = str(ch.get("title") or "").strip()
        sec = _ts_to_sec(ch.get("time") or "")
        if not title or sec < 0:
            continue
        if total_dur and sec > total_dur + 1:
            continue
        cleaned.append({"sec": sec, "title": title})
    cleaned.sort(key=lambda c: c["sec"])
    out: list[dict] = []
    for ch in cleaned:
        if not out:
            ch["sec"] = 0  # 第一個一律 0:00（YouTube 章節要求）
            out.append(ch)
        elif ch["sec"] - out[-1]["sec"] >= 10:
            out.append(ch)
    if out and out[0]["sec"] != 0:
        out[0]["sec"] = 0
    return out


# ---- 組文件 ------------------------------------------------------------------

def _glossary_terms(ep: Episode) -> list[str]:
    """episode.yaml glossary 的 canonical（含 note）→ 名詞對照清單。"""
    items = config.dedup_glossary(config.normalize_glossary(ep.cfg.get("glossary") or []))
    terms = []
    for it in items:
        canon = str(it.get("canonical") or "").strip()
        if not canon:
            continue
        note = str(it.get("note") or "").strip()
        terms.append(f"{canon}（{note}）" if note else canon)
    return terms


def render_doc(data: dict, ctx: dict, meta: dict) -> str:
    """把模型 JSON + 確定性欄位拼成整份上架文案 .txt。"""
    show = ctx.get("show") or ""
    hosts = "、".join(ctx.get("hosts") or [])
    guest = ctx.get("guest") or ""
    name = meta["name"]
    dur_str = meta["duration_str"]
    chapters = meta["chapters"]
    glossary_terms = meta["glossary_terms"]

    L = []
    header = f"{name}｜上架文案"
    if show:
        header = f"{show} ft. {guest}｜上架文案" if guest else f"{show}｜上架文案"
    L.append(header)
    L.append("=" * 40)
    line = f"成品：{name}_YT完整版.mp4（{dur_str}、1080p）"
    if guest:
        line += f"｜來賓：{guest}"
    if hosts:
        line += f"｜主持：{hosts}"
    L.append(line)
    L.append("章節時間戳為成品時間軸（已含倍速、去空拍、片頭偏移）。")
    L.append("")

    # 一、標題
    L.append("── 一、影片標題 ─────────────────────────")
    L.append(f"★ 推薦　{data.get('title_recommended', '').strip()}")
    alts = data.get("title_alternatives") or []
    if alts:
        L.append("備選")
        for a in alts:
            L.append(f"　• {str(a).strip()}")
    L.append("")

    # 二、說明欄
    L.append("── 二、YouTube 說明欄（整段選取複製）──────")
    L.append(str(data.get("description", "")).strip())
    L.append("")
    L.append("▍章節時間軸")
    for ch in chapters:
        L.append(f"{_fmt_ts(ch['sec'])} {ch['title']}")
    L.append("")
    hilites = data.get("highlights") or []
    if hilites:
        L.append("▍本集重點")
        for h in hilites:
            L.append(f"・{str(h).strip()}")
        L.append("")
    tags = data.get("hashtags") or []
    if tags:
        L.append("　".join(f"#{str(t).strip().lstrip('#')}" for t in tags))
    L.append("")

    # 三、章節（單獨版）
    L.append("── 三、章節時間軸（單獨版・方便檢查）──────")
    for ch in chapters:
        L.append(f"{_fmt_ts(ch['sec'])}\t{ch['title']}")
    L.append("時間軸已對齊成品時間軸（扣掉頭尾修剪、刪段、倍速）。")
    L.append("")

    # 四、社群短文
    social = str(data.get("social", "")).strip()
    if social:
        L.append("── 四、社群短文（IG／FB）────────────────")
        L.append(social)
        L.append("")

    # 五、待補欄位
    L.append("── 五、待補欄位（上架前確認）────────────")
    L.append("☐ EP 編號（標題用）")
    for link in ctx.get("links") or []:
        L.append(f"☐ 連結：{link}")
    if not (ctx.get("links")):
        L.append("☐ 相關連結（報名／延伸媒體等，如有）")
    if not hosts:
        L.append("☐ 主持人掛名（episode.yaml publish.hosts 未設）")
    L.append("")

    # 名詞對照
    if glossary_terms:
        L.append("── 重要名詞對照（避免打錯）──────────────")
        L.append("、".join(glossary_terms))
        L.append("")

    return "\n".join(L).rstrip() + "\n"


def generate(
    episode_dir: Path,
    *,
    model: str | None = None,
    timeout: int = 600,
    out_path: Path | None = None,
    ep_number: str | None = None,
) -> Path:
    """產生上架文案 .txt，回輸出路徑。"""
    ep = Episode(episode_dir)

    srt_text, total_dur = build_final_timeline_srt(episode_dir)
    transcript = _transcript_for_prompt(srt_text)
    ctx = _show_context(ep)
    if ep_number:
        ctx["ep_number"] = ep_number

    print("→ 呼叫本地 Claude Code 產文案（讀成品逐字稿）…")
    data = _run_claude(build_prompt(transcript, ctx), model=model, timeout=timeout)

    chapters = _normalize_chapters(data.get("chapters") or [], total_dur)
    if not chapters:
        raise PublishDocError("模型未回傳可用章節（chapters 為空或格式錯）")

    meta = {
        "name": ep.name,
        "duration_str": _fmt_ts(total_dur),
        "chapters": chapters,
        "glossary_terms": _glossary_terms(ep),
    }
    text = render_doc(data, ctx, meta)

    if out_path is None:
        out_path = ep.subdir("output") / f"{ep.name}_上架文案.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path


def run(episode_dir: Path, *, model: str | None = None, ep_number: str | None = None) -> int:
    """CLI 進入點。"""
    try:
        out = generate(episode_dir, model=model, ep_number=ep_number)
    except PublishDocError as e:
        print(f"✗ 上架文案失敗：{e}")
        return 1
    print(f"✓ 上架文案已產生：{out}")
    return 0
