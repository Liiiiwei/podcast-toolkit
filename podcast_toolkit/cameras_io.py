"""字幕卡 → 鏡頭對應的 sidecar JSON 讀寫。

存在 _v2.srt 旁邊 _v2.cameras.json：
- key = 字幕卡 idx（int）
- value = "a" | "b"

只記 explicit 標記過的卡；沒記的由消費端用 carry-forward 預設值補。
空 mapping 不寫檔（避免噪音），有檔就刪掉。
"""
from __future__ import annotations
import json
from pathlib import Path


def load(path: Path) -> dict[int, str]:
    """讀 sidecar；不存在 → 回空 dict。"""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): str(v) for k, v in raw.items()}


def save(path: Path, mapping: dict[int, str]) -> None:
    """寫 sidecar；mapping 空就把舊檔刪掉。"""
    if not mapping:
        if path.exists():
            path.unlink()
        return
    serializable = {str(int(k)): str(v) for k, v in mapping.items()}
    path.write_text(
        json.dumps(serializable, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
