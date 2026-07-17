"""全域設定（~/.podcast-toolkit/config.json）：API keys、STT provider、episode roots。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from podcast_toolkit.web import transcribe as transcribe_mod
from podcast_toolkit.web.shared import RouteContext, _check_assets_status

# 預設 STT 供應商（單一事實來源）：零雲端金鑰政策，預設走本地 Breeze。
# /api/transcribe（routes/transcribe.py）與 /api/config 的收斂值都吃這個常數，
# 兩邊不准各寫各的字串。
DEFAULT_STT_PROVIDER = "breeze"

# provider 對應的 config key（雲端 provider 缺 key 視同不可用）
_CLOUD_KEY_MAP = {
    "xai": "xai_api_key",
    "gemini": "gemini_api_key",
    "openai": "openai_api_key",
}


def _public_provider(cfg: dict) -> str:
    """對外收斂 provider：
    - 未設定 / 未知 → 預設 breeze（本地、免金鑰）
    - 雲端 provider（xai/gemini/openai）但沒設對應 key → 不可用，收斂成 breeze
    - 本地（breeze / whisper_mlx）或「雲端且有 key」（進階使用者手改 config.json）→ 原樣
    """
    provider = (cfg.get("transcribe") or {}).get("provider") or DEFAULT_STT_PROVIDER
    if provider == DEFAULT_STT_PROVIDER:
        return provider
    if provider not in transcribe_mod.PROVIDERS:
        return DEFAULT_STT_PROVIDER
    key_name = _CLOUD_KEY_MAP.get(provider)
    if key_name and not cfg.get(key_name):
        return DEFAULT_STT_PROVIDER
    return provider


def _breeze_status() -> dict:
    """本地 Breeze 引擎是否就緒（給 dashboard／設定的狀態 pill 用）。"""
    from podcast_toolkit.web import transcribe_job

    bdir = transcribe_job._breeze_dir()
    return {"available": bdir is not None, "dir": str(bdir) if bdir else None}


def _config_payload(cfg: dict) -> dict:
    return {
        "has_xai_api_key": bool(cfg.get("xai_api_key")),
        "has_gemini_api_key": bool(cfg.get("gemini_api_key")),
        "has_openai_api_key": bool(cfg.get("openai_api_key")),
        "provider": _public_provider(cfg),
        "breeze": _breeze_status(),
        "episode_roots": cfg.get("episode_roots") or [str(Path.home() / "Downloads")],
        "assets": _check_assets_status(),
    }


def register(app: FastAPI, ctx: RouteContext) -> None:

    @app.get("/api/config")
    def get_config():
        return JSONResponse(_config_payload(ctx.load_config()))

    @app.post("/api/config")
    def post_config(payload: dict):
        cfg = ctx.load_config()
        for key_name in ("xai_api_key", "gemini_api_key", "openai_api_key"):
            if key_name in payload:
                key = (payload.get(key_name) or "").strip()
                if key:
                    cfg[key_name] = key
                else:
                    cfg.pop(key_name, None)
        if "provider" in payload:
            provider = (payload.get("provider") or "").strip()
            # breeze 走獨立 job（/api/transcribe/breeze），不在 PROVIDERS 分流表裡，
            # 但它是產品預設值，必須讓設定頁存得進去
            if provider != DEFAULT_STT_PROVIDER and provider not in transcribe_mod.PROVIDERS:
                raise HTTPException(
                    status_code=400, detail=f"未知的 STT 供應商：{provider}"
                )
            tcfg = cfg.get("transcribe") or {}
            tcfg["provider"] = provider
            cfg["transcribe"] = tcfg
        if "episode_roots" in payload:
            roots = payload.get("episode_roots")
            if not isinstance(roots, list) or not all(isinstance(x, str) for x in roots):
                raise HTTPException(status_code=400, detail="episode_roots 必須是字串陣列")
            cfg["episode_roots"] = [r.strip() for r in roots if r.strip()]
        ctx.save_config(cfg)
        return JSONResponse(_config_payload(cfg))
