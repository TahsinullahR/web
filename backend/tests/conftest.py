"""Shared fixtures: isolated tmp SQLite DB per test session, mocked provider."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    # Reset cached settings so env changes take effect per-test
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import create_app
    application = create_app()
    return application


@pytest.fixture()
async def client(app):
    from httpx import ASGITransport, AsyncClient
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
