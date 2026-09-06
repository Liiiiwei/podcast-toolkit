"""本機模型 sidecar 清單檢查。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

def _write_manifest(path: Path, model_hash: str) -> None:
    data = {
        "manifest_version": 1,
        "sidecar_version": "qwen3-4b-q4-k-m-llama-cpp-0.4.0",
        "architecture": "arm64",
        "required": [
            {"path": "bin/llama-cli", "type": "executable", "sha256": None},
            {"path": "lib", "type": "directory", "sha256": None},
            {
                "path": "models/Qwen3-4B-Q4_K_M.gguf",
                "type": "file",
                "sha256": model_hash,
            },
            {"path": "licenses/Qwen3-LICENSE", "type": "file", "sha256": None},
            {"path": "licenses/llama.cpp-LICENSE", "type": "file", "sha256": None},
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def _stage(tmp_path: Path) -> tuple[Path, Path]:
    stage = tmp_path / "local-llm-stage"
    binary = stage / "bin" / "llama-cli"
    model = stage / "models" / "Qwen3-4B-Q4_K_M.gguf"
    qwen_license = stage / "licenses" / "Qwen3-LICENSE"
    llama_license = stage / "licenses" / "llama.cpp-LICENSE"
    for path in (binary, model, qwen_license, llama_license):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stub")
    binary.chmod(0o755)
    (stage / "lib").mkdir()
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, hashlib.sha256(b"stub").hexdigest())
    return stage, manifest


def test_manifest_accepts_complete_portable_stage(tmp_path):
    from scripts.check_local_llm_stage import check_stage

    stage, manifest = _stage(tmp_path)
    assert check_stage(stage, manifest, machine="arm64") == []


def test_manifest_rejects_non_executable_runtime(tmp_path):
    from scripts.check_local_llm_stage import check_stage

    stage, manifest = _stage(tmp_path)
    (stage / "bin" / "llama-cli").chmod(0o644)
    errors = check_stage(stage, manifest, machine="arm64")
    assert any("不可執行" in error for error in errors)


def test_manifest_rejects_wrong_architecture(tmp_path):
    from scripts.check_local_llm_stage import check_stage

    stage, manifest = _stage(tmp_path)
    errors = check_stage(stage, manifest, machine="x86_64")
    assert any("架構不符" in error for error in errors)


def test_manifest_rejects_wrong_model_hash(tmp_path):
    from scripts.check_local_llm_stage import check_stage

    stage, manifest = _stage(tmp_path)
    (stage / "models" / "Qwen3-4B-Q4_K_M.gguf").write_bytes(b"changed")
    errors = check_stage(stage, manifest, machine="arm64")
    assert any("雜湊不符" in error for error in errors)


def test_manifest_allows_hashed_model_symlink_for_space_saving(tmp_path):
    """stage 可連到已驗證模型，打包時才複製實體檔。"""
    from scripts.check_local_llm_stage import check_stage

    stage, manifest = _stage(tmp_path)
    model = stage / "models" / "Qwen3-4B-Q4_K_M.gguf"
    external_model = tmp_path / "model-cache.gguf"
    external_model.write_bytes(model.read_bytes())
    model.unlink()
    model.symlink_to(external_model)

    assert check_stage(stage, manifest, machine="arm64") == []
