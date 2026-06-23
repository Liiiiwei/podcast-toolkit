"""把 Breeze ASR 產出的 SRT 匯入成 toolkit 的 _final_v2.srt + speakers.json。

Breeze(聯發科 Whisper-large-v2 微調,本地跑)出的字幕已 jieba 斷好、帶逐字時間;
含講者版每行前綴 `[MicN]`(Mic1/Mic2/Mic3 = 主持/老闆/來賓)。這支:
- 去掉 `[MicN]` 標籤 → 乾淨 `_final_v2.srt`(下游編輯/合成統一入口)
- 把 MicN → speaker key(a/b/c…)→ `_final_v2.speakers.json`(cameras_io 格式),
  餵給自動鏡頭(AP2)與編輯器講者標。

這是「額外的匯入入口」,不動原本的雲端轉錄 / resegment 流程;非 Breeze 使用者零影響。
"""
from __future__ import annotations

import glob
import re
import shutil
import sys
from pathlib import Path

from podcast_toolkit import cameras_io, srt_io
from podcast_toolkit.episode import Episode
from podcast_toolkit.subtitle_cleanup import destrand_cards, smooth_speakers

# 行首 [Mic1] / [Mic 1] / [郝慧川] 之類的講者標籤
_LABEL_RE = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*")
# 預設 Mic → speaker key(對齊 toolkit a/b/c 慣例;非 MicN 標籤按出現序自動配字母)
_DEFAULT_MIC_MAP = {"Mic1": "a", "Mic2": "b", "Mic3": "c", "Mic4": "d", "Mic5": "e"}
# 找 Breeze 字幕的檔名樣式(優先含講者 / 最終)
_BREEZE_GLOBS = ("*含講者*.srt", "*最終字幕*.srt", "*_字幕*.srt")


def parse_label(text: str) -> tuple[str | None, str]:
    """抽出行首 `[標籤]` → (label or None, 去標籤後文字)。"""
    m = _LABEL_RE.match(text)
    if not m:
        return None, text.strip()
    return m.group(1).strip(), text[m.end():].strip()


def find_breeze_srt(folder: Path) -> Path | None:
    """在集資料夾找 Breeze 字幕(優先含講者 / 最新 mtime)。找不到回 None。"""
    for pat in _BREEZE_GLOBS:
        hits = sorted(glob.glob(str(folder / pat)),
                      key=lambda p: Path(p).stat().st_mtime, reverse=True)
        if hits:
            return Path(hits[0])
    return None


def ingest(srt_path, *, mic_map=None) -> tuple[list[dict], dict[int, str]]:
    """解析 Breeze SRT → (cards 去標籤+重編號, speakers {idx: key})。純函式,不碰輸出檔。"""
    mic_map = {**_DEFAULT_MIC_MAP, **(mic_map or {})}
    raw = srt_io.parse(Path(srt_path).read_text(encoding="utf-8"))
    cards: list[dict] = []
    labels: list[str | None] = []
    for n, c in enumerate(raw, 1):
        label, text = parse_label(c["text"])
        cards.append({"idx": n, "start": c["start"], "end": c["end"], "text": text})
        labels.append(label)

    # label → speaker key:MicN 走 mic_map;其餘(真名等)按首次出現序配 a/b/c…
    # 只避開「本檔實際用到」的 key(不是所有可能的 mic_map 字母),純真名檔才會乾淨拿到 a/b/c。
    used: set[str] = set()
    name_key: dict[str, str] = {}
    speakers: dict[int, str] = {}
    for n, label in enumerate(labels, 1):
        if not label:
            continue
        norm = re.sub(r"\s+", "", label)
        if norm in mic_map:
            key = mic_map[norm]
        elif label in name_key:
            key = name_key[label]
        else:
            c = ord("a")
            while chr(c) in used:
                c += 1
            key = chr(c)
            name_key[label] = key
        used.add(key)
        speakers[n] = key
    return cards, speakers


def run(episode_dir, *, srt=None, mic_map=None, force=False, cleanup=True) -> int:
    """CLI 進入點:Breeze SRT → 寫 _final_v2.srt + speakers.json(先備份既有)。回 exit code。

    cleanup=True(預設):匯入後自動跑講者平滑 + 去甩尾(subtitle_cleanup)——
    修逐卡麥能量翻錯標(同一人切成不同講者)+ 斷句把句尾名詞甩到下一卡。對沒問題的集是 no-op。
    """
    ep = Episode(Path(episode_dir))
    src = Path(srt) if srt else find_breeze_srt(ep.dir)
    if not src or not src.exists():
        print("✗ 找不到 Breeze 字幕(用 --srt 指定,或確認集資料夾有 *含講者*.srt)", file=sys.stderr)
        return 3

    cards, speakers = ingest(src, mic_map=mic_map)
    if not cards:
        print(f"✗ {src.name} 解析不出任何字幕卡", file=sys.stderr)
        return 1

    if cleanup:
        # 講者平滑要先做（去掉短 blip）→ 去甩尾才用乾淨的講者判斷「同講者才挪」
        speakers = smooth_speakers(cards, speakers)
        destrand_cards(cards, speakers)

    v2 = ep.output_v2_srt()
    spk_path = ep.output_v2_speakers_json()
    v2.parent.mkdir(parents=True, exist_ok=True)
    if v2.exists():
        shutil.copy(v2, v2.with_name(f"{v2.stem}.pre-breeze.bak{v2.suffix}"))
    if spk_path.exists():
        shutil.copy(spk_path, spk_path.with_name(spk_path.name + ".pre-breeze.bak"))

    v2.write_text(srt_io.serialize(cards), encoding="utf-8")
    cameras_io.save(spk_path, speakers)   # 空 dict 會自動刪舊檔(無講者版)

    n_people = len(set(speakers.values()))
    tag = (f" + {spk_path.name}({len(speakers)} 卡標講者 / {n_people} 人)"
           if speakers else "(無講者標籤)")
    print(f"匯入 Breeze:{src.name} → {v2.name}({len(cards)} 卡){tag}")
    return 0
