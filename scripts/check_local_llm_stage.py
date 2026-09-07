#!/usr/bin/env python3
"""檢查本機模型 sidecar 是否具備可打包的完整資產。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from pathlib import Path


DEFAULT_MANIFEST = Path(__file__).with_name("local-llm-stage-manifest.json")
EXPECTED_VERSION = "qwen3-4b-q4-k-m-llama-cpp-0.4.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_stage(
    stage: Path,
    manifest_path: Path,
    *,
    machine: str | None = None,
) -> list[str]:
    """回傳所有資產錯誤；空陣列代表可安全打包。"""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"無法讀取本機模型清單：{manifest_path}（{exc}）"]

    errors: list[str] = []
    if manifest.get("sidecar_version") != EXPECTED_VERSION:
        errors.append(
            f"本機模型版本不符：清單為 {manifest.get('sidecar_version')!r}，"
            f"需要 {EXPECTED_VERSION}"
        )
    actual_machine = machine or platform.machine()
    expected_machine = str(manifest.get("architecture") or "")
    if actual_machine != expected_machine:
        errors.append(f"本機模型架構不符：清單為 {expected_machine}，目前為 {actual_machine}")

    stage_root = stage.resolve()
    for item in manifest.get("required", []):
        relative = Path(str(item.get("path", "")))
        expected_hash = item.get("sha256")
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"清單路徑逸出 sidecar：{relative}")
            continue
        target = stage / relative
        if target.is_symlink() and not expected_hash:
            errors.append(f"外部符號連結缺少雜湊：{relative}")
            continue
        if not target.is_symlink():
            try:
                target.resolve().relative_to(stage_root)
            except ValueError:
                errors.append(f"清單路徑逸出 sidecar：{relative}")
                continue
        kind = item.get("type")
        exists = target.is_dir() if kind == "directory" else target.is_file()
        if not exists:
            errors.append(f"本機模型缺件：{target}")
            continue
        if kind == "executable" and not os.access(target, os.X_OK):
            errors.append(f"本機模型推論器不可執行：{target}")
        if expected_hash and target.is_file():
            actual_hash = _sha256(target)
            if actual_hash != expected_hash:
                errors.append(
                    f"本機模型雜湊不符：{relative}（預期 {expected_hash}，實際 {actual_hash}）"
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    errors = check_stage(args.stage.expanduser(), args.manifest)
    if errors:
        print("✗ 本機模型 sidecar 檢查失敗：", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"✓ 本機模型 sidecar 資產齊備：{args.stage.expanduser()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
