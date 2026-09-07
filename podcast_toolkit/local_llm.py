"""內建 `llama.cpp` 本機模型執行器。"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from podcast_toolkit import config


MODEL_FILENAME = "Qwen3-4B-Q4_K_M.gguf"


class LocalLLMError(RuntimeError):
    """本機模型缺件、逾時、執行失敗或輸出格式錯誤。"""


def runtime_paths() -> tuple[Path, Path]:
    """回傳推論器與模型路徑；環境變數優先於 App 內建資源。"""
    root = config.toolkit_root() / "local-llm"
    binary = Path(os.environ.get("PODCAST_TOOLKIT_LLM_BIN") or root / "bin" / "llama-cli")
    model = Path(
        os.environ.get("PODCAST_TOOLKIT_LLM_MODEL")
        or root / "models" / MODEL_FILENAME
    )
    return binary.expanduser(), model.expanduser()


def is_available() -> bool:
    """內建推論器與模型均存在時才算可用。"""
    binary, model = runtime_paths()
    return binary.is_file() and os.access(binary, os.X_OK) and model.is_file()


def _extract_json(text: str):
    """從模型輸出抽出第一個完整 JSON 物件或陣列。"""
    if "\nAssistant:\n" in text:
        text = text.rsplit("\nAssistant:\n", 1)[1]
    decoder = json.JSONDecoder()
    for position, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[position:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value
    raise LocalLLMError(f"本機模型輸出找不到合法 JSON：{text[:200]}")


def complete_json(
    prompt: str,
    *,
    timeout: int = 600,
    max_tokens: int = 2048,
    context_size: int = 8192,
    temperature: float = 0.1,
):
    """完全離線執行提示詞並回傳 JSON；不允許模型網址或自動下載。"""
    binary, model = runtime_paths()
    if not is_available():
        raise LocalLLMError(
            "找不到完整本機模型執行環境："
            f"推論器={binary}，模型={model}。請重新安裝含本機模型的 App。"
        )

    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="podcast-llm-", suffix=".txt", delete=False) as tmp:
            output_path = Path(tmp.name)
        cmd = [
            str(binary),
            "-m", str(model),
            "--offline",
            "-ngl", "all",
            "-c", str(max(512, int(context_size))),
            "-n", str(max(16, int(max_tokens))),
            "-st",
            "--reasoning", "off",
            "--no-display-prompt",
            "--no-show-timings",
            "--simple-io",
            "--log-disable",
            "--temp", str(max(0.0, float(temperature))),
            "--seed", "42",
            "-o", str(output_path),
            "-p", prompt,
        ]
        env = os.environ.copy()
        bundled_root = binary.parent.parent
        bundled_lib = binary.parent.parent / "lib"
        if bundled_lib.is_dir():
            old = env.get("DYLD_LIBRARY_PATH", "")
            env["DYLD_LIBRARY_PATH"] = os.pathsep.join(
                part for part in (str(bundled_lib), old) if part
            )
        bundled_backends = bundled_root / "libexec"
        if bundled_backends.is_dir():
            env["GGML_BACKEND_PATH"] = str(bundled_backends)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise LocalLLMError(f"本機模型推論逾時（{timeout} 秒）") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()[:400]
            raise LocalLLMError(f"本機模型推論失敗（rc={proc.returncode}）：{detail}")
        raw = output_path.read_text(encoding="utf-8", errors="replace")
        return _extract_json(raw)
    finally:
        if output_path is not None:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass
