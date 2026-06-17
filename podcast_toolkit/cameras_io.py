"""鏡頭對應的 sidecar JSON 讀寫。

鏡頭已改成**時間版**（與字幕脫鉤，換字幕/重切不會錯位）：
- 新格式：{"version": 2, "transitions": [{"t": 秒(母帶/_v2 時間軸), "cam": "a"|"b"}, ...]}
  transitions 只記「切換點」（carry-forward：第一個切換前用 default_cam）。
- 舊格式（向後相容）：{ "<卡 idx>": "a"|"b", ... } —— 讀到會用當下 _v2 卡換算成時間。

load()/save()（flat idx→str）保留給 speakers sidecar 與舊檔讀取；
鏡頭請走 load_transitions()/save_transitions()。空 transitions 不寫檔。
"""
from __future__ import annotations
import bisect
import json
from pathlib import Path


def load(path: Path) -> dict[int, str]:
    """讀 flat idx→str sidecar（speakers / 舊鏡頭格式用）；不存在 → 回空 dict。"""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "transitions" in raw:
        # 新時間版鏡頭檔不該走這條（speakers 不會是這格式）；保險回空
        return {}
    return {int(k): str(v) for k, v in raw.items()}


def save(path: Path, mapping: dict[int, str]) -> None:
    """寫 flat idx→str sidecar（speakers 用）；mapping 空就把舊檔刪掉。"""
    if not mapping:
        if path.exists():
            path.unlink()
        return
    serializable = {str(int(k)): str(v) for k, v in mapping.items()}
    path.write_text(
        json.dumps(serializable, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )


# ── 時間版鏡頭 transition ──────────────────────────────────────────────

def card_mapping_to_transitions(
    mapping: dict[int, str], cards: list[dict], default_cam: str = "a"
) -> list[dict]:
    """卡 idx→cam（explicit 標記）→ 時間版切換點 [{t, cam}]。

    複製 carry-forward 語意：依 idx 排序走卡，explicit 與當前 cam 不同才產生一個
    切換點（時間 = 該卡 start）。連續同 cam 的標記自然不重複產生切換。
    """
    by_idx = sorted(cards, key=lambda c: c["idx"])
    out: list[dict] = []
    current = default_cam
    for c in by_idx:
        ex = mapping.get(int(c["idx"]))
        if ex and ex != current:
            out.append({"t": float(c["start"]), "cam": str(ex)})
            current = ex
    return out


def suggest_camera_cuts(
    speakers: dict[int, str],
    cards: list[dict],
    home_cam: str = "a",
    feature_cam: dict[str, str] | None = None,
    min_sec: float = 15.0,
) -> list[dict]:
    """依「home 鏡頭 + 特定講者連續講夠久才切到他的鏡頭」規則，建議時間版切換點 [{t, cam}]。

    符合觀察到的實際剪法（不是 naive「跟著講者切」）：預設待在 home_cam（wide/主持）；
    只有 feature_cam 裡的講者（例：來賓 c → cam b）**連續講滿 min_sec 秒**，才把那整段
    切到他的鏡頭，講完回 home；不在 feature_cam 的講者一律留 home。
    （沈奕妤集實測 min_sec=15：建議 58 切點 vs 人工 56、逐卡吻合 99%；naive 版只有 24%。）

    建議值非鎖定：使用者仍可在編輯器覆蓋少數例外。
    """
    feature_cam = feature_cam or {}
    if not speakers or not cards:
        return []
    ordered = sorted(cards, key=lambda c: float(c["start"]))
    out: list[dict] = []
    current = home_cam
    i, n = 0, len(ordered)
    while i < n:
        spk = speakers.get(int(ordered[i]["idx"]))
        j = i  # 收攏連續同一講者的卡，量整段連續時長
        while j + 1 < n and speakers.get(int(ordered[j + 1]["idx"])) == spk:
            j += 1
        seg_dur = float(ordered[j]["end"]) - float(ordered[i]["start"])
        want = feature_cam[spk] if (spk in feature_cam and seg_dur >= min_sec) else home_cam
        if want != current:
            out.append({"t": float(ordered[i]["start"]), "cam": want})
            current = want
        i = j + 1
    return out


def transitions_to_card_mapping(
    transitions: list[dict], cards: list[dict]
) -> dict[int, str]:
    """時間版切換點 → 卡 idx→cam（給前端顯示用）。

    每個切換點的時間吸附到「start 最接近」的卡，標在那張卡上。
    換字幕後用新斷句的卡來吸附 → 切換點自動落到新斷句最近的卡。
    """
    pairs = sorted((float(c["start"]), int(c["idx"])) for c in cards)
    starts = [p[0] for p in pairs]
    mapping: dict[int, str] = {}
    for tr in transitions:
        t = float(tr["t"])
        i = bisect.bisect_left(starts, t)
        cand = [j for j in (i, i - 1) if 0 <= j < len(starts)]
        if not cand:
            continue
        best = min(cand, key=lambda j: abs(starts[j] - t))
        mapping[pairs[best][1]] = str(tr["cam"])
    return mapping


def load_transitions(path: Path, cards: list[dict]) -> list[dict]:
    """讀鏡頭切換點 [{t, cam}]。新格式直接讀；舊 idx→cam 用 cards 換算（自動遷移讀取）。"""
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "transitions" in raw:
        return [{"t": float(x["t"]), "cam": str(x["cam"])} for x in raw["transitions"]]
    legacy = {int(k): str(v) for k, v in raw.items()}
    return card_mapping_to_transitions(legacy, cards)


def save_transitions(path: Path, transitions: list[dict]) -> None:
    """寫時間版鏡頭切換點；空就刪檔。"""
    if not transitions:
        if path.exists():
            path.unlink()
        return
    data = {
        "version": 2,
        "transitions": [
            {"t": round(float(tr["t"]), 3), "cam": str(tr["cam"])}
            for tr in sorted(transitions, key=lambda x: float(x["t"]))
        ],
    }
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
