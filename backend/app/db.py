"""Async SQLite persistence layer (aiosqlite) with a small migration runner.

Schema migrations live in ./migrations as numbered .sql files so the same
process can later be pointed at PostgreSQL (see docs/ARCHITECTURE.md).
"""
from __future__ import annotations

import re
from pathlib import Path

import aiosqlite

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def get_connection(db_path: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migration_files() -> list[tuple[int, Path]]:
    out = []
    if MIGRATIONS_DIR.exists():
        for f in MIGRATIONS_DIR.glob("*.sql"):
            m = re.match(r"^(\d+)_", f.name)
            if m:
                out.append((int(m.group(1)), f))
    return sorted(out)


async def run_migrations(conn: aiosqlite.Connection) -> list[str]:
    """Apply any not-yet-applied migrations. Returns applied file names."""
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " name TEXT PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')))"
    )
    cur = await conn.execute("SELECT name FROM schema_migrations")
    applied = {r["name"] for r in await cur.fetchall()}
    newly: list[str] = []
    for _, path in _migration_files():
        if path.name in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        await conn.executescript(sql)
        await conn.execute(
            "INSERT INTO schema_migrations (name) VALUES (?)", (path.name,)
        )
        newly.append(path.name)
    await conn.commit()
    return newly
