"""全域設定（~/.podcast-toolkit/config.json）：API keys、STT provider、episode roots。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from podcast_toolkit.web import transcribe as transcribe_mod
from podcast_toolkit.web.shared import RouteContext, _check_assets_status


def register(app: FastAPI, ctx: RouteContext) -> None:

    @app.get("/api/config")
    def get_config():
        cfg = ctx.load_config()
        # xai 已從設定面板下架；舊 config 殘留 "xai" 一律回退 gemini
        provider = (cfg.get("transcribe") or {}).get("provider") or "gemini"
        if provider not in transcribe_mod.PROVIDERS or provider == "xai":
            provider = "gemini"
        return JSONResponse({
            "has_xai_api_key": bool(cfg.get("xai_api_key")),
            "has_gemini_api_key": bool(cfg.get("gemini_api_key")),
            "has_openai_api_key": bool(cfg.get("openai_api_key")),
            "provider": provider,
            "episode_roots": cfg.get("episode_roots") or [str(Path.home() / "Downloads")],
            "assets": _check_assets_status(),
        })

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
            if provider not in transcribe_mod.PROVIDERS:
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
        out_provider = (cfg.get("transcribe") or {}).get("provider") or "gemini"
        if out_provider == "xai":
            out_provider = "gemini"
        return JSONResponse({
            "has_xai_api_key": bool(cfg.get("xai_api_key")),
            "has_gemini_api_key": bool(cfg.get("gemini_api_key")),
            "has_openai_api_key": bool(cfg.get("openai_api_key")),
            "provider": out_provider,
            "episode_roots": cfg.get("episode_roots") or [str(Path.home() / "Downloads")],
            "assets": _check_assets_status(),
        })
