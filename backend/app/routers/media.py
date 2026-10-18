"""Creative Studio: honest provider status + real local generation where possible.

Providers:
- image: Pollinations.ai (free, no key) — real HTTP integration.
- tts: Windows PowerShell System.Speech via subprocess is NOT used from the web
  server; instead we expose a clearly-labelled unavailable state unless
  EDGE_TTS style providers are configured. No fake success ever.
- video / stt / other image providers: adapters exist but report
  "not_connected" with required configuration, per the free-first honesty rule.
"""
from __future__ import annotations

import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import repo
from ..config import Settings
from ..schemas import MediaGenerateIn

router = APIRouter(prefix="/api/media", tags=["creative"])

ASSETS_ROOT = None  # set via app.state.assets_dir in lifespan


def _providers(settings: Settings) -> list[dict]:
    return [
        {
            "id": "pollinations",
            "kind": "image",
            "label": "Pollinations AI (text-to-image)",
            "connected": True,
            "free": True,
            "limits": "Rate-limited public service; images ~1024px; no API key needed.",
            "requires": "",
        },
        {
            "id": "openrouter-chat",
            "kind": "text",
            "label": "OpenRouter text generation",
            "connected": bool(settings.openrouter_api_key),
            "free": True,
            "limits": "Free models only by default; subject to OpenRouter quotas.",
            "requires": "" if settings.openrouter_api_key else "OPENROUTER_API_KEY",
        },
        {
            "id": "local-tts",
            "kind": "tts",
            "label": "Text-to-speech",
            "connected": False,
            "free": True,
            "limits": "No TTS engine is installed/configured on this server yet.",
            "requires": "Configure a TTS provider (e.g. Piper local or a cloud key) in .env",
        },
        {
            "id": "video-gen",
            "kind": "video",
            "label": "Video generation",
            "connected": False,
            "free": False,
            "limits": "No genuinely free no-key video API verified.",
            "requires": "A compatible video provider API (none connected).",
        },
        {
            "id": "stt",
            "kind": "stt",
            "label": "Speech-to-text",
            "connected": False,
            "free": True,
            "limits": "Requires a local model (e.g. whisper.cpp) which is not installed here.",
            "requires": "Install/point to a local Whisper runtime.",
        },
    ]


@router.get("/providers")
async def providers(request: Request):
    return {"providers": _providers(request.app.state.settings)}


@router.get("/jobs")
async def jobs(request: Request, limit: int = 100):
    return await repo.list_media_jobs(request.app.state.conn, min(limit, 500))


@router.post("/generate")
async def generate(body: MediaGenerateIn, request: Request):
    conn = request.app.state.conn
    assets_dir = request.app.state.assets_dir

    if body.kind == "image":
        prompt = body.prompt.strip()[:1000]
        url = ("https://image.pollinations.ai/prompt/"
               f"{httpx.QueryParams({'p': prompt})['p']}?width=768&height=512&nologo=true&seed={uuid.uuid4().int % 99999}")
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.get(url)
                ctype = r.headers.get("content-type", "")
                if r.status_code != 200 or not ctype.startswith("image/"):
                    detail = f"HTTP {r.status_code} {ctype}"
                    await repo.add_media_job(conn, "image", "pollinations", prompt,
                                             "failed", detail)
                    raise HTTPException(502, f"Image provider failed ({detail}). Try again later.")
                fname = f"{uuid.uuid4().hex}.jpg"
                (assets_dir / fname).write_bytes(r.content)
                job = await repo.add_media_job(conn, "image", "pollinations", prompt,
                                               "completed", f"{len(r.content)} bytes", fname)
                return job
        except httpx.HTTPError as e:
            await repo.add_media_job(conn, "image", "pollinations", body.prompt,
                                     "failed", f"network: {type(e).__name__}")
            raise HTTPException(502, "Image provider unreachable (network error).")

    # Non-connected kinds: honest failure, recorded in history.
    prov = next((p for p in _providers(request.app.state.settings) if p["kind"] == body.kind), None)
    detail = (prov["limits"] + " Requires: " + prov["requires"]) if prov else "Provider not found"
    await repo.add_media_job(conn, body.kind, prov["id"] if prov else "none",
                             body.prompt, "unavailable", detail)
    raise HTTPException(503, f"No real {body.kind} provider is connected. {detail}")


@router.get("/assets/{fname}")
async def get_asset(fname: str, request: Request):
    if not fname.endswith(".jpg") or "/" in fname or ".." in fname:
        raise HTTPException(400, "Invalid asset name")
    path = request.app.state.assets_dir / fname
    if not path.is_file():
        raise HTTPException(404, "Asset not found")
    return FileResponse(path, media_type="image/jpeg", filename=fname)
