#!/usr/bin/env python3
"""檢查 Breeze sidecar 是否具備可打包的完整資產。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = Path(__file__).with_name("breeze-stage-manifest.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_stage(stage: Path, manifest_path: Path, *, strict_hash: bool = False) -> list[str]:
    """回傳錯誤；必要資產缺少或雜湊不符時由呼叫端決定是否中止。"""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"無法讀取 sidecar 清單：{manifest_path}（{exc}）"]

    errors: list[str] = []
    if manifest.get("sidecar_version") != "breeze-asr-25":
        errors.append(
            f"sidecar 版本不符：清單為 {manifest.get('sidecar_version')!r}，需要 breeze-asr-25"
        )
    for item in manifest.get("required", []):
        relative = Path(str(item.get("path", "")))
        target = (stage / relative).resolve()
        try:
            target.relative_to(stage.resolve())
        except ValueError:
            errors.append(f"清單路徑逸出 sidecar：{relative}")
            continue
        kind = item.get("type")
        exists = target.is_dir() if kind == "directory" else target.is_file()
        if not exists:
            errors.append(f"sidecar 缺件：{target}")
            continue
        expected = item.get("sha256")
        if expected:
            actual = _sha256(target)
            if actual != expected:
                errors.append(f"sidecar 雜湊不符：{relative}（預期 {expected}，實際 {actual}）")
        elif strict_hash and kind == "file":
            errors.append(f"sidecar 尚未釘定雜湊：{relative}；請先產生正式清單")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--strict-hash", action="store_true")
    args = parser.parse_args(argv)
    errors = check_stage(args.stage.expanduser(), args.manifest, strict_hash=args.strict_hash)
    if errors:
        print("✗ Breeze sidecar 檢查失敗：", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"✓ Breeze sidecar 資產齊備：{args.stage.expanduser()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
