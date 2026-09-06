"""內建本機模型執行器測試。"""

from __future__ import annotations

from pathlib import Path

import pytest


def _fake_runtime(tmp_path: Path, *, output: str, returncode: int = 0) -> tuple[Path, Path]:
    """建立真實可執行替身；缺少離線參數時主動失敗。"""
    binary = tmp_path / "bin" / "llama-cli"
    binary.parent.mkdir()
    (tmp_path / "libexec").mkdir()
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import sys\n"
        "if not os.environ.get('GGML_BACKEND_PATH'):\n"
        "    raise SystemExit(94)\n"
        "args = sys.argv[1:]\n"
        "if '--offline' not in args or '--reasoning' not in args:\n"
        "    raise SystemExit(91)\n"
        "if args[args.index('--reasoning') + 1] != 'off':\n"
        "    raise SystemExit(92)\n"
        "if '-o' not in args:\n"
        "    raise SystemExit(93)\n"
        "from pathlib import Path\n"
        f"Path(args[args.index('-o') + 1]).write_text({output!r}, encoding='utf-8')\n"
        f"raise SystemExit({returncode})\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    model = tmp_path / "Qwen3-4B-Q4_K_M.gguf"
    model.write_bytes(b"model")
    return binary, model


def test_complete_json_runs_real_subprocess_in_offline_mode(tmp_path, monkeypatch):
    """若漏掉離線或關閉思考參數，替身會以非零碼中止。"""
    from podcast_toolkit import local_llm

    binary, model = _fake_runtime(
        tmp_path,
        output='啟動資訊\n```json\n{"answer":"完成"}\n```\n結束',
    )
    monkeypatch.setenv("PODCAST_TOOLKIT_LLM_BIN", str(binary))
    monkeypatch.setenv("PODCAST_TOOLKIT_LLM_MODEL", str(model))

    assert local_llm.complete_json("只回 JSON", max_tokens=32) == {"answer": "完成"}


def test_complete_json_accepts_top_level_array(tmp_path, monkeypatch):
    """字幕校對的固定欄位陣列可以直接解析。"""
    from podcast_toolkit import local_llm

    binary, model = _fake_runtime(
        tmp_path,
        output='載入模型\n[{"idx":1,"text":"郝慧川"}]\n完成',
    )
    monkeypatch.setenv("PODCAST_TOOLKIT_LLM_BIN", str(binary))
    monkeypatch.setenv("PODCAST_TOOLKIT_LLM_MODEL", str(model))

    assert local_llm.complete_json("校對", max_tokens=32) == [
        {"idx": 1, "text": "郝慧川"}
    ]


def test_runtime_paths_finds_bundled_resources(tmp_path, monkeypatch):
    """正式 App 只靠 Contents/Resources/local-llm 即可找到完整執行環境。"""
    from podcast_toolkit import local_llm

    root = tmp_path / "Resources"
    binary = root / "local-llm" / "bin" / "llama-cli"
    model = root / "local-llm" / "models" / "Qwen3-4B-Q4_K_M.gguf"
    binary.parent.mkdir(parents=True)
    model.parent.mkdir(parents=True)
    binary.write_bytes(b"bin")
    model.write_bytes(b"model")
    binary.chmod(0o755)
    monkeypatch.delenv("PODCAST_TOOLKIT_LLM_BIN", raising=False)
    monkeypatch.delenv("PODCAST_TOOLKIT_LLM_MODEL", raising=False)
    monkeypatch.setattr(local_llm.config, "toolkit_root", lambda: root)

    assert local_llm.runtime_paths() == (binary, model)
    assert local_llm.is_available() is True


def test_missing_runtime_returns_actionable_error(tmp_path, monkeypatch):
    """缺模型時不可靜默跳過或回傳空結果。"""
    from podcast_toolkit import local_llm

    monkeypatch.setenv("PODCAST_TOOLKIT_LLM_BIN", str(tmp_path / "missing-bin"))
    monkeypatch.setenv("PODCAST_TOOLKIT_LLM_MODEL", str(tmp_path / "missing-model"))

    with pytest.raises(local_llm.LocalLLMError, match="本機模型"):
        local_llm.complete_json("測試")


def test_nonzero_runtime_exit_is_an_error(tmp_path, monkeypatch):
    """推論器失敗不能被當成合法空結果。"""
    from podcast_toolkit import local_llm

    binary, model = _fake_runtime(tmp_path, output="推論失敗", returncode=7)
    monkeypatch.setenv("PODCAST_TOOLKIT_LLM_BIN", str(binary))
    monkeypatch.setenv("PODCAST_TOOLKIT_LLM_MODEL", str(model))

    with pytest.raises(local_llm.LocalLLMError, match="rc=7"):
        local_llm.complete_json("測試")


def test_invalid_json_is_an_error(tmp_path, monkeypatch):
    """模型只回一般文字時不得進入下游寫檔。"""
    from podcast_toolkit import local_llm

    binary, model = _fake_runtime(tmp_path, output="沒有結構化資料")
    monkeypatch.setenv("PODCAST_TOOLKIT_LLM_BIN", str(binary))
    monkeypatch.setenv("PODCAST_TOOLKIT_LLM_MODEL", str(model))

    with pytest.raises(local_llm.LocalLLMError, match="JSON"):
        local_llm.complete_json("測試")
