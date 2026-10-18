"""API tests: health, conversation CRUD, validation, persistence, streaming."""
from __future__ import annotations

import json

import pytest


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["provider_configured"] is True
    # Never leak the key:
    assert "test-key-not-real" not in r.text


async def test_conversation_crud(client):
    # create
    r = await client.post("/api/conversations", json={"title": "Hello", "model": "openrouter/free"})
    assert r.status_code == 201
    cid = r.json()["id"]

    # list
    r = await client.get("/api/conversations")
    assert r.status_code == 200 and len(r.json()) == 1

    # get
    r = await client.get(f"/api/conversations/{cid}")
    assert r.json()["title"] == "Hello"

    # rename
    r = await client.patch(f"/api/conversations/{cid}", json={"title": "Renamed"})
    assert r.json()["title"] == "Renamed"

    # delete
    r = await client.delete(f"/api/conversations/{cid}")
    assert r.status_code == 204
    r = await client.get(f"/api/conversations/{cid}")
    assert r.status_code == 404


async def test_delete_missing_404(client):
    r = await client.delete("/api/conversations/nope")
    assert r.status_code == 404


async def test_blank_message_rejected(client):
    r = await client.post("/api/conversations", json={"title": "x"})
    cid = r.json()["id"]
    r = await client.post(f"/api/chat/{cid}/stream", json={"content": "   "})
    assert r.status_code == 422


async def test_chat_to_missing_conversation(client):
    r = await client.post("/api/chat/missing/stream", json={"content": "hi"})
    assert r.status_code == 404


async def test_streaming_chat_persists(client, monkeypatch):
    """Mock the OpenRouter stream; verify SSE deltas + DB persistence."""
    from app import openrouter as ormod

    async def fake_stream(self, messages, model):
        for tok in ["Hel", "lo ", "wor", "ld"]:
            yield tok

    monkeypatch.setattr(ormod.OpenRouterAdapter, "stream_chat", fake_stream)

    r = await client.post("/api/conversations", json={"title": "New chat"})
    cid = r.json()["id"]
    r = await client.post(f"/api/chat/{cid}/stream",
                          json={"content": "say hello", "model": "test/model"})
    assert r.status_code == 200
    deltas = []
    done = False
    for line in r.text.splitlines():
        if line.startswith("data: "):
            evt = json.loads(line[6:])
            if "delta" in evt:
                deltas.append(evt["delta"])
            if evt.get("done"):
                done = True
    assert "".join(deltas) == "Hello world"
    assert done

    # Persisted? user + assistant messages present, auto-title applied.
    r = await client.get(f"/api/conversations/{cid}/messages")
    msgs = r.json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "Hello world"
    r = await client.get(f"/api/conversations/{cid}")
    assert r.json()["title"] == "say hello"


async def test_stream_provider_error_surface(client, monkeypatch):
    from app import openrouter as ormod

    async def failing_stream(self, messages, model):
        raise ormod.ProviderError("rate_limited", "Rate limited by OpenRouter", 429)
        yield ""  # make it an async generator

    monkeypatch.setattr(ormod.OpenRouterAdapter, "stream_chat", failing_stream)

    r = await client.post("/api/conversations", json={})
    cid = r.json()["id"]
    r = await client.post(f"/api/chat/{cid}/stream", json={"content": "hi"})
    assert r.status_code == 200
    assert '"rate_limited"' in r.text


async def test_regenerate(client, monkeypatch):
    from app import openrouter as ormod

    async def fake_stream(self, messages, model):
        yield "answer"

    monkeypatch.setattr(ormod.OpenRouterAdapter, "stream_chat", fake_stream)

    r = await client.post("/api/conversations", json={})
    cid = r.json()["id"]
    await client.post(f"/api/chat/{cid}/stream", json={"content": "q1"})
    r = await client.post(f"/api/chat/{cid}/regenerate")
    assert r.status_code == 200
    r = await client.get(f"/api/conversations/{cid}/messages")
    assert [m["role"] for m in r.json()] == ["user"]
    # Nothing left to regenerate
    r = await client.post(f"/api/chat/{cid}/regenerate")
    assert r.status_code == 409


async def test_models_endpoint_mocked(client, monkeypatch):
    from app import openrouter as ormod

    async def fake_list(self):
        return [{"id": "a/free", "name": "Free A", "context_length": 8192,
                 "is_free": True, "architecture": {}},
                {"id": "b/paid", "name": "Paid B", "context_length": 4096,
                 "is_free": False, "architecture": {}}]

    monkeypatch.setattr(ormod.OpenRouterAdapter, "list_models", fake_list)
    r = await client.get("/api/models")
    body = r.json()
    assert body["total"] == 2 and body["free_count"] == 1
    assert body["models"][0]["id"] == "a/free"  # free first


def test_pricing_is_free_only_when_zero():
    from app.openrouter import _to_float
    assert _to_float("0") == 0.0
    assert _to_float(None) == -1.0
