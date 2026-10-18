"""Model catalogue + provider configuration status endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..openrouter import OpenRouterAdapter, ProviderError

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
async def list_models(request: Request):
    adapter: OpenRouterAdapter = request.app.state.openrouter
    try:
        models = await adapter.list_models()
    except ProviderError as e:
        return JSONResponse(e.to_dict(), status_code=e.status or 502)
    free_count = sum(1 for m in models if m["is_free"])
    return {"models": models, "total": len(models), "free_count": free_count,
            "default_model": adapter.s.openrouter_model}
