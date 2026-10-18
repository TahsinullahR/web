"""Data-access layer for conversations & messages (SQLite via aiosqlite)."""
from __future__ import annotations

import uuid

import aiosqlite


async def create_conversation(conn: aiosqlite.Connection, title: str, model: str) -> dict:
    cid = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO conversations (id, title, model) VALUES (?, ?, ?)",
        (cid, title, model),
    )
    await conn.commit()
    return await get_conversation(conn, cid)


async def get_conversation(conn: aiosqlite.Connection, cid: str) -> dict | None:
    cur = await conn.execute(
        "SELECT id, title, model, created_at, updated_at FROM conversations WHERE id = ?",
        (cid,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def list_conversations(conn: aiosqlite.Connection, limit: int = 100,
                             offset: int = 0) -> list[dict]:
    cur = await conn.execute(
        "SELECT id, title, model, created_at, updated_at FROM conversations "
        "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return [dict(r) for r in await cur.fetchall()]


async def update_conversation(conn: aiosqlite.Connection, cid: str, *,
                              title: str | None = None,
                              model: str | None = None) -> dict | None:
    if title is not None:
        await conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, cid))
    if model is not None:
        await conn.execute("UPDATE conversations SET model = ? WHERE id = ?", (model, cid))
    await conn.execute(
        "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?", (cid,)
    )
    await conn.commit()
    return await get_conversation(conn, cid)


async def delete_conversation(conn: aiosqlite.Connection, cid: str) -> bool:
    cur = await conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
    await conn.commit()
    return cur.rowcount > 0


async def add_message(conn: aiosqlite.Connection, cid: str, role: str,
                      content: str, model: str = "") -> dict:
    cur = await conn.execute(
        "INSERT INTO messages (conversation_id, role, content, model) VALUES (?, ?, ?, ?)",
        (cid, role, content, model),
    )
    await conn.execute(
        "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?", (cid,)
    )
    await conn.commit()
    cur2 = await conn.execute(
        "SELECT id, role, content, model, created_at FROM messages WHERE id = ?",
        (cur.lastrowid,),
    )
    return dict(await cur2.fetchone())


async def get_messages(conn: aiosqlite.Connection, cid: str) -> list[dict]:
    cur = await conn.execute(
        "SELECT id, role, content, model, created_at FROM messages "
        "WHERE conversation_id = ? ORDER BY id ASC",
        (cid,),
    )
    return [dict(r) for r in await cur.fetchall()]


async def latest_assistant_message(conn: aiosqlite.Connection, cid: str) -> dict | None:
    cur = await conn.execute(
        "SELECT id, role, content, model, created_at FROM messages "
        "WHERE conversation_id = ? AND role = 'assistant' ORDER BY id DESC LIMIT 1",
        (cid,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def delete_latest_assistant(conn: aiosqlite.Connection, cid: str) -> None:
    """Used by regenerate: drop the last assistant turn before re-asking."""
    await conn.execute(
        "DELETE FROM messages WHERE id = ("
        " SELECT id FROM messages WHERE conversation_id = ? AND role='assistant'"
        " ORDER BY id DESC LIMIT 1)",
        (cid,),
    )
    await conn.commit()


# ---- Re-export platform data layer so callers can `from .. import repo` uniformly ----
from .repo_platform import (  # noqa: E402,F401
    add_log, add_media_job, create_project, create_task, current_files,
    delete_device, delete_project, file_versions, get_file_version, get_logs,
    get_project, get_setting, get_step, get_steps, get_task, list_devices,
    list_media_jobs, list_projects, list_tasks, rename_project, save_project_file,
    set_setting, set_step_status, set_task_status, update_project_status, upsert_device,
)
