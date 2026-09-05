"""Breeze sidecar 清單檢查。"""

import json
from pathlib import Path

from scripts.check_breeze_stage import check_stage


MANIFEST = Path(__file__).parents[1] / "scripts" / "breeze-stage-manifest.json"


def _stage(tmp_path: Path) -> Path:
    stage = tmp_path / "breeze-stage"
    for item in (
        "py-runtime/bin/python3.9",
        "make_subtitle.py",
        "srt_segment.py",
        "rhythm_segment.py",
        "dict.txt.big",
        "cache/whisper/breeze-asr-25.pt",
    ):
        path = stage / item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stub")
    (stage / "site-packages").mkdir(parents=True)
    return stage


def test_manifest_accepts_required_stage(tmp_path):
    assert check_stage(_stage(tmp_path), MANIFEST) == []


def test_manifest_reports_missing_asset(tmp_path):
    stage = _stage(tmp_path)
    (stage / "dict.txt.big").unlink()
    errors = check_stage(stage, MANIFEST)
    assert any("dict.txt.big" in error for error in errors)


def test_manifest_rejects_wrong_hash(tmp_path):
    stage = _stage(tmp_path)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["required"][2]["sha256"] = "0" * 64
    custom = tmp_path / "manifest.json"
    custom.write_text(json.dumps(manifest), encoding="utf-8")
    errors = check_stage(stage, custom)
    assert any("雜湊不符" in error for error in errors)
