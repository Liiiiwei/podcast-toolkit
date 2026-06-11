"""transcribe_per_mic：N 路 mic → VAD → Gemini → N 個 SRT 檔。

不打真 Gemini / 不跑真 ffmpeg；monkeypatch 兩個邊界當作 stub，
聚焦驗 orchestrator 的流程：mic 缺檔 raise / SRT 寫對位置 / glossary 注入 prompt。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from podcast_toolkit import gemini_subtitle, vad_gate
from podcast_toolkit.episode import Episode


CANNED_SRT_A = """\
1
00:00:00,000 --> 00:00:02,000
講者 a 的台詞
"""

CANNED_SRT_B = """\
1
00:00:01,000 --> 00:00:03,000
講者 b 的台詞
"""


def _setup_mics(tmp_episode_dir: Path, mic_keys=("a", "b")) -> Path:
    """加 mics 到 episode.yaml + 建空 mic 檔在 01_母帶/。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    mics_block = "mics:\n"
    for k in mic_keys:
        mics_block += f"  {k}: 01_母帶/{{name}}_mic{k.upper()}.wav\n"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8") + mics_block,
        encoding="utf-8",
    )
    for k in mic_keys:
        (tmp_episode_dir / "01_母帶" / f"測試集_mic{k.upper()}.wav").write_bytes(b"FAKE")
    return tmp_episode_dir


@pytest.fixture
def stub_vad_and_gemini(monkeypatch):
    """攔 vad_gate.gate_audio_file（變成 touch output）+ gemini transcribe（回 canned）。

    回傳呼叫紀錄 dict，讓測試 assert 呼叫順序 / 參數。
    """
    calls = {"vad": [], "gemini": []}

    def fake_gate(input_path, output_path, **kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"GATED")
        calls["vad"].append({
            "input": Path(input_path).name,
            "output": Path(output_path).name,
            **kwargs,
        })
        return Path(output_path)

    def fake_transcribe(audio_path, prompt, model):
        calls["gemini"].append({
            "audio": Path(audio_path).name,
            "model": model,
            "prompt_has_glossary": "Liwei Sia" in prompt,
        })
        # 從檔名判斷 speaker 回不同 canned SRT
        name = Path(audio_path).name
        if "_a." in name:
            return CANNED_SRT_A
        if "_b." in name:
            return CANNED_SRT_B
        return CANNED_SRT_A

    monkeypatch.setattr(vad_gate, "gate_audio_file", fake_gate)
    monkeypatch.setattr(gemini_subtitle, "transcribe", fake_transcribe)
    return calls


# --- 基本流程 ---


def test_transcribe_per_mic_raises_when_no_mics_set(tmp_episode_dir):
    """沒設 mics → 拒絕跑（不要跌進無 mic 的混音軌路徑）。"""
    ep = Episode(tmp_episode_dir)
    with pytest.raises(RuntimeError, match="mics"):
        gemini_subtitle.transcribe_per_mic(ep)


def test_transcribe_per_mic_writes_one_srt_per_mic(tmp_episode_dir, stub_vad_and_gemini):
    """兩路 mic → 寫出 04_工作檔/{name}_mic_a.srt 與 _mic_b.srt。"""
    _setup_mics(tmp_episode_dir, ("a", "b"))
    ep = Episode(tmp_episode_dir)

    result = gemini_subtitle.transcribe_per_mic(ep)

    srt_a = tmp_episode_dir / "04_工作檔" / "測試集_mic_a.srt"
    srt_b = tmp_episode_dir / "04_工作檔" / "測試集_mic_b.srt"
    assert srt_a.is_file()
    assert srt_b.is_file()
    assert "講者 a 的台詞" in srt_a.read_text(encoding="utf-8")
    assert "講者 b 的台詞" in srt_b.read_text(encoding="utf-8")
    assert result == {"a": srt_a, "b": srt_b}


def test_transcribe_per_mic_gates_before_uploading(tmp_episode_dir, stub_vad_and_gemini):
    """VAD gate 一定要在 Gemini 上傳前發生（Gemini 拿到的是 gated 檔，不是原 mic）。"""
    _setup_mics(tmp_episode_dir, ("a",))
    ep = Episode(tmp_episode_dir)

    gemini_subtitle.transcribe_per_mic(ep)

    assert len(stub_vad_and_gemini["vad"]) == 1
    assert len(stub_vad_and_gemini["gemini"]) == 1
    # gemini 拿到的是 gated 輸出檔名，不是原 mic 檔名
    assert stub_vad_and_gemini["gemini"][0]["audio"] == "測試集_micgate_a.wav"


def test_transcribe_per_mic_passes_vad_params_from_cfg(tmp_episode_dir, stub_vad_and_gemini):
    """VAD 參數要從 cfg.per_mic 傳進去（不能硬寫）。"""
    _setup_mics(tmp_episode_dir, ("a",))
    # episode override 部分 VAD 參數
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "per_mic:\n  vad_threshold: 0.05\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)

    gemini_subtitle.transcribe_per_mic(ep)

    call = stub_vad_and_gemini["vad"][0]
    assert call["threshold"] == 0.05               # episode override
    assert call["min_speech_sec"] == 0.3           # defaults
    assert call["pad_sec"] == 0.15                 # defaults


def test_transcribe_per_mic_injects_glossary_into_prompt(tmp_episode_dir, stub_vad_and_gemini):
    """glossary 要進 prompt（驗證 build_prompt 流程沒被 per-mic 旁路掉）。"""
    _setup_mics(tmp_episode_dir, ("a",))
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "glossary:\n  - Liwei Sia\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)

    gemini_subtitle.transcribe_per_mic(ep)

    assert stub_vad_and_gemini["gemini"][0]["prompt_has_glossary"] is True


def test_transcribe_per_mic_raises_when_mic_file_missing(tmp_episode_dir):
    """mics 設了路徑但檔案不在 → 明確 raise，不要靜默跳過。

    Stage 6a 後改為 aggregate raise：單軌錯誤包成 RuntimeError 但訊息保留
    原始類型 + 訊息，所以「找不到 mic 檔案」字串仍會出現。
    """
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "mics:\n  a: 01_母帶/不存在.wav\n",
        encoding="utf-8",
    )
    ep = Episode(tmp_episode_dir)
    with pytest.raises(RuntimeError, match="FileNotFoundError.*mic"):
        gemini_subtitle.transcribe_per_mic(ep)


# --- force / dry_run / skip-existing 行為 ---


def test_transcribe_per_mic_skips_existing_without_force(tmp_episode_dir, stub_vad_and_gemini):
    """已存在的 SRT 不重跑（avoid 浪費 Gemini quota）。"""
    _setup_mics(tmp_episode_dir, ("a",))
    # 預先放 SRT
    existing = tmp_episode_dir / "04_工作檔" / "測試集_mic_a.srt"
    existing.write_text("舊內容", encoding="utf-8")
    ep = Episode(tmp_episode_dir)

    gemini_subtitle.transcribe_per_mic(ep, force=False)

    # Gemini 不應該被呼叫
    assert stub_vad_and_gemini["gemini"] == []
    # 舊檔保留
    assert existing.read_text(encoding="utf-8") == "舊內容"


def test_transcribe_per_mic_force_overwrites_existing(tmp_episode_dir, stub_vad_and_gemini):
    """--force → 覆寫，重新呼叫 Gemini。"""
    _setup_mics(tmp_episode_dir, ("a",))
    existing = tmp_episode_dir / "04_工作檔" / "測試集_mic_a.srt"
    existing.write_text("舊內容", encoding="utf-8")
    ep = Episode(tmp_episode_dir)

    gemini_subtitle.transcribe_per_mic(ep, force=True)

    assert len(stub_vad_and_gemini["gemini"]) == 1
    assert "講者 a 的台詞" in existing.read_text(encoding="utf-8")


def test_transcribe_per_mic_dry_run_skips_gemini_call(tmp_episode_dir, stub_vad_and_gemini):
    """--dry-run 不應呼叫 Gemini API（也不應寫 SRT）。"""
    _setup_mics(tmp_episode_dir, ("a",))
    ep = Episode(tmp_episode_dir)

    gemini_subtitle.transcribe_per_mic(ep, dry_run=True)

    assert stub_vad_and_gemini["gemini"] == []
    assert not (tmp_episode_dir / "04_工作檔" / "測試集_mic_a.srt").exists()
