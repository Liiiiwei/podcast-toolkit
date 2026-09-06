"""本機模型 sidecar 注入 App 的行為測試。"""

from __future__ import annotations

from pathlib import Path


def test_copy_stage_resolves_model_symlink_and_preserves_executable(tmp_path):
    """換機後 App 不可仍指向打包電腦的模型快取。"""
    from scripts.copy_local_llm_stage import copy_stage

    stage = tmp_path / "stage"
    binary = stage / "bin" / "llama-cli"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"binary")
    binary.chmod(0o755)
    (stage / "lib").mkdir()
    (stage / "lib" / "runtime.dylib").write_bytes(b"library")
    (stage / "libexec").mkdir()
    (stage / "libexec" / "backend.so").write_bytes(b"backend")
    (stage / "licenses").mkdir()
    (stage / "licenses" / "LICENSE").write_text("license", encoding="utf-8")
    (stage / "models").mkdir()
    cached_model = tmp_path / "cached-model.gguf"
    cached_model.write_bytes(b"model")
    (stage / "models" / "Qwen3-4B-Q4_K_M.gguf").symlink_to(cached_model)

    target = tmp_path / "AppResources" / "local-llm"
    copy_stage(stage, target)

    copied_model = target / "models" / "Qwen3-4B-Q4_K_M.gguf"
    assert copied_model.read_bytes() == b"model"
    assert not copied_model.is_symlink()
    assert (target / "bin" / "llama-cli").stat().st_mode & 0o111
    assert (target / "lib" / "runtime.dylib").read_bytes() == b"library"
    assert (target / "libexec" / "backend.so").read_bytes() == b"backend"
