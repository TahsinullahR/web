"""OpenRouter provider adapter.

All OpenRouter traffic goes through this module only. The API key never leaves
the backend process. Handles timeouts, retries with exponential backoff, rate
limits, invalid keys and unavailable models, mapping them to structured errors.
"""
from __future__ import annotations

import asyncio
import json
import random
from typing import AsyncIterator

import httpx

from .config import Settings


class ProviderError(Exception):
    """Structured provider error safe to expose to the client."""

    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code = code          # invalid_key | rate_limited | unavailable_model |
                                 # timeout | provider_error | not_configured
        self.message = message
        self.status = status

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message,
                          "status": self.status}}


class OpenRouterAdapter:
    def __init__(self, settings: Settings):
        self.s = settings

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.s.openrouter_api_key}",
            "Content-Type": "application/json",
            # Attribution headers recommended by OpenRouter docs
            "HTTP-Referer": "http://localhost:3000",
            "X-Title": "NOVA AI",
        }

    def _ensure_configured(self) -> None:
        if not self.s.openrouter_api_key:
            raise ProviderError(
                "not_configured",
                "OPENROUTER_API_KEY is not set on the server. Add it to backend/.env.",
            )

    @staticmethod
    def _classify(resp: httpx.Response) -> ProviderError:
        status = resp.status_code
        detail = ""
        try:
            body = resp.json()
            detail = body.get("error", {}).get("message", "") or json.dumps(body)[:300]
        except Exception:
            detail = resp.text[:300]
        if status in (401, 403):
            return ProviderError("invalid_key",
                                 f"Invalid or unauthorized API key: {detail}", status)
        if status == 429:
            return ProviderError("rate_limited",
                                 f"Rate limited by OpenRouter: {detail}", status)
        if status == 404:
            return ProviderError("unavailable_model",
                                 f"Model not available: {detail}", status)
        return ProviderError("provider_error",
                             f"Provider error (HTTP {status}): {detail}", status)

    async def _request_with_backoff(
        self, client: httpx.AsyncClient, method: str, url: str, **kwargs
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.s.max_retries):
            try:
                resp = await client.request(method, url, **kwargs)
                if resp.status_code == 429 and attempt < self.s.max_retries - 1:
                    # Retry rate-limited requests with exponential backoff + jitter
                    await asyncio.sleep(min(2 ** attempt + random.random(), 8))
                    continue
                if resp.status_code >= 400:
                    raise self._classify(resp)
                return resp
            except httpx.TimeoutException as e:
                last_exc = e
                if attempt < self.s.max_retries - 1:
                    await asyncio.sleep(min(2 ** attempt, 8))
                    continue
                raise ProviderError("timeout",
                                    "OpenRouter request timed out. Try again.") from e
        raise ProviderError("provider_error",
                            f"Request failed after retries: {last_exc}")

    async def list_models(self) -> list[dict]:
        """Fetch the full model catalogue (public endpoint; no key required)."""
        async with httpx.AsyncClient(timeout=self.s.request_timeout_seconds) as client:
            resp = await self._request_with_backoff(
                client, "GET", f"{self.s.openrouter_base_url}/models"
            )
        data = resp.json().get("data", [])
        models = []
        for m in data:
            pricing = (m.get("pricing") or {})
            prompt = _to_float(pricing.get("prompt"))
            completion = _to_float(pricing.get("completion"))
            is_free = prompt == 0.0 and completion == 0.0
            models.append({
                "id": m.get("id", ""),
                "name": m.get("name", m.get("id", "")),
                "context_length": m.get("context_length"),
                "is_free": is_free,  # verified from current pricing metadata only
                "architecture": {
                    "modality": (m.get("architecture") or {}).get("modality"),
                    "input_modalities": (m.get("architecture") or {}).get("input_modalities"),
                },
            })
        models.sort(key=lambda x: (not x["is_free"], x["id"]))
        return models

    async def stream_chat(
        self, messages: list[dict], model: str
    ) -> AsyncIterator[str]:
        """Yield text deltas from a streaming chat completion."""
        self._ensure_configured()
        payload = {"model": model, "messages": messages, "stream": True}
        timeout = httpx.Timeout(self.s.request_timeout_seconds, connect=10)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.s.openrouter_base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                ) as resp:
                    if resp.status_code >= 400:
                        await resp.aread()
                        raise self._classify(resp)
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
            except httpx.TimeoutException as e:
                raise ProviderError("timeout",
                                    "OpenRouter stream timed out.") from e


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return -1.0
