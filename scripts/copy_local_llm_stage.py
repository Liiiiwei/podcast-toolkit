#!/usr/bin/env python3
"""把本機模型 stage 注入 App，並把模型符號連結展開為實體檔。"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _copy_model(source: Path, target: Path) -> None:
    """同一個 APFS 卷優先使用寫入時複製；不支援時退回一般複製。"""
    if sys.platform == "darwin":
        proc = subprocess.run(
            ["cp", "-c", str(source.resolve()), str(target)],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            shutil.copystat(source, target, follow_symlinks=True)
            return
    shutil.copy2(source, target, follow_symlinks=True)


def copy_stage(stage: Path, target: Path) -> None:
    """複製完整 sidecar；模型一律跟隨連結，確保 App 可攜。"""
    target.mkdir(parents=True, exist_ok=True)
    for name in ("bin", "lib", "libexec", "licenses"):
        shutil.copytree(stage / name, target / name, dirs_exist_ok=True, symlinks=False)
    model_target = target / "models"
    model_target.mkdir(parents=True, exist_ok=True)
    for source in (stage / "models").iterdir():
        if source.is_file():
            _copy_model(source, model_target / source.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args(argv)
    copy_stage(args.stage.expanduser(), args.target.expanduser())
    print(f"✓ 本機模型已注入：{args.target.expanduser()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
