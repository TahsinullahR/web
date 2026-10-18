"""Data-access layer additions: agent tasks, projects, media jobs, devices, settings."""
from __future__ import annotations

import json
import uuid

import aiosqlite


# ---------- Agent tasks ----------

async def create_task(conn: aiosqlite.Connection, task_id: str, instruction: str,
                      plan: list[dict]) -> dict:
    await conn.execute(
        "INSERT INTO agent_tasks (id, instruction, status, plan_json) VALUES (?, ?, 'planning', ?)",
        (task_id, instruction, json.dumps(plan)),
    )
    for i, st in enumerate(plan):
        await conn.execute(
            "INSERT INTO task_steps (task_id, idx, description, tool, args_json, needs_approval)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, i, st["description"], st.get("tool", "none"),
             json.dumps(st.get("args", {})), 1 if st.get("needs_approval") else 0),
        )
    await conn.execute("UPDATE agent_tasks SET status='queued' WHERE id=?", (task_id,))
    await conn.commit()
    return await get_task(conn, task_id)


async def get_task(conn: aiosqlite.Connection, tid: str) -> dict | None:
    cur = await conn.execute(
        "SELECT * FROM agent_tasks WHERE id = ?", (tid,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def list_tasks(conn: aiosqlite.Connection, limit: int = 50, offset: int = 0) -> list[dict]:
    cur = await conn.execute(
        "SELECT * FROM agent_tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset))
    return [dict(r) for r in await cur.fetchall()]


async def set_task_status(conn: aiosqlite.Connection, tid: str, status: str,
                          summary: str | None = None, error: str | None = None) -> None:
    await conn.execute(
        "UPDATE agent_tasks SET status=?, updated_at=datetime('now'),"
        " result_summary=COALESCE(?, result_summary), error=COALESCE(?, error) WHERE id=?",
        (status, summary, error, tid))
    await conn.commit()


async def get_steps(conn: aiosqlite.Connection, tid: str) -> list[dict]:
    cur = await conn.execute(
        "SELECT * FROM task_steps WHERE task_id=? ORDER BY idx ASC", (tid,))
    return [dict(r) for r in await cur.fetchall()]


async def get_step(conn: aiosqlite.Connection, step_id: int) -> dict | None:
    cur = await conn.execute("SELECT * FROM task_steps WHERE id=?", (step_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def set_step_status(conn: aiosqlite.Connection, step_id: int, status: str,
                          output: str | None = None) -> None:
    await conn.execute(
        "UPDATE task_steps SET status=?, updated_at=datetime('now'),"
        " output=COALESCE(?, output) WHERE id=?", (status, output, step_id))
    await conn.commit()


async def add_log(conn: aiosqlite.Connection, tid: str, level: str, message: str) -> None:
    await conn.execute(
        "INSERT INTO task_logs (task_id, level, message) VALUES (?, ?, ?)",
        (tid, level, message[:2000]))
    await conn.commit()


async def get_logs(conn: aiosqlite.Connection, tid: str, limit: int = 200) -> list[dict]:
    cur = await conn.execute(
        "SELECT id, level, message, created_at FROM task_logs WHERE task_id=?"
        " ORDER BY id ASC LIMIT ?", (tid, limit))
    return [dict(r) for r in await cur.fetchall()]


# ---------- Projects ----------

async def create_project(conn: aiosqlite.Connection, pid: str, name: str, kind: str,
                         prompt: str) -> dict:
    await conn.execute(
        "INSERT INTO projects (id, name, kind, prompt) VALUES (?, ?, ?, ?)",
        (pid, name, kind, prompt))
    await conn.commit()
    return await get_project(conn, pid)


async def get_project(conn: aiosqlite.Connection, pid: str) -> dict | None:
    cur = await conn.execute("SELECT * FROM projects WHERE id=?", (pid,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def list_projects(conn: aiosqlite.Connection) -> list[dict]:
    cur = await conn.execute("SELECT * FROM projects ORDER BY updated_at DESC")
    return [dict(r) for r in await cur.fetchall()]


async def update_project_status(conn: aiosqlite.Connection, pid: str, status: str,
                                build_log: str | None = None) -> None:
    await conn.execute(
        "UPDATE projects SET status=?, updated_at=datetime('now'),"
        " build_log=COALESCE(?, build_log) WHERE id=?", (status, build_log, pid))
    await conn.commit()


async def rename_project(conn: aiosqlite.Connection, pid: str, name: str) -> None:
    await conn.execute(
        "UPDATE projects SET name=?, updated_at=datetime('now') WHERE id=?", (name, pid))
    await conn.commit()


async def delete_project(conn: aiosqlite.Connection, pid: str) -> bool:
    cur = await conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    await conn.commit()
    return cur.rowcount > 0


async def save_project_file(conn: aiosqlite.Connection, pid: str, path: str,
                            content: str) -> int:
    """Insert a new version; returns the version number."""
    cur = await conn.execute(
        "SELECT COALESCE(MAX(version), 0) v FROM project_files WHERE project_id=? AND path=?",
        (pid, path))
    version = (await cur.fetchone())["v"] + 1
    await conn.execute(
        "INSERT INTO project_files (project_id, path, content, version) VALUES (?, ?, ?, ?)",
        (pid, path, content, version))
    await conn.execute("UPDATE projects SET updated_at=datetime('now') WHERE id=?", (pid,))
    await conn.commit()
    return version


async def current_files(conn: aiosqlite.Connection, pid: str) -> list[dict]:
    cur = await conn.execute(
        "SELECT path, content, MAX(version) version, updated_at FROM project_files"
        " WHERE project_id=? GROUP BY path ORDER BY path", (pid,))
    return [dict(r) for r in await cur.fetchall()]


async def file_versions(conn: aiosqlite.Connection, pid: str, path: str) -> list[dict]:
    cur = await conn.execute(
        "SELECT version, length(content) size, updated_at FROM project_files"
        " WHERE project_id=? AND path=? ORDER BY version DESC", (pid, path))
    return [dict(r) for r in await cur.fetchall()]


async def get_file_version(conn: aiosqlite.Connection, pid: str, path: str,
                           version: int) -> str | None:
    cur = await conn.execute(
        "SELECT content FROM project_files WHERE project_id=? AND path=? AND version=?",
        (pid, path, version))
    row = await cur.fetchone()
    return row["content"] if row else None


# ---------- Media jobs ----------

async def add_media_job(conn: aiosqlite.Connection, kind: str, provider: str,
                        prompt: str, status: str, detail: str,
                        asset_path: str = "") -> dict:
    jid = uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO media_jobs (id, kind, provider, prompt, status, detail, asset_path)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (jid, kind, provider, prompt[:2000], status, detail[:1000], asset_path))
    await conn.commit()
    cur = await conn.execute("SELECT * FROM media_jobs WHERE id=?", (jid,))
    return dict(await cur.fetchone())


async def list_media_jobs(conn: aiosqlite.Connection, limit: int = 100) -> list[dict]:
    cur = await conn.execute(
        "SELECT * FROM media_jobs ORDER BY created_at DESC LIMIT ?", (limit,))
    return [dict(r) for r in await cur.fetchall()]


# ---------- Devices & settings ----------

async def list_devices(conn: aiosqlite.Connection) -> list[dict]:
    cur = await conn.execute("SELECT * FROM devices ORDER BY created_at DESC")
    return [dict(r) for r in await cur.fetchall()]


async def upsert_device(conn: aiosqlite.Connection, did: str, name: str,
                        platform: str, paired: bool) -> dict:
    await conn.execute(
        "INSERT INTO devices (id, name, platform, paired, last_seen) VALUES (?, ?, ?, ?, datetime('now'))"
        " ON CONFLICT(id) DO UPDATE SET name=excluded.name, platform=excluded.platform,"
        " paired=excluded.paired, last_seen=datetime('now')",
        (did, name, platform, 1 if paired else 0))
    await conn.commit()
    cur = await conn.execute("SELECT * FROM devices WHERE id=?", (did,))
    return dict(await cur.fetchone())


async def delete_device(conn: aiosqlite.Connection, did: str) -> bool:
    cur = await conn.execute("DELETE FROM devices WHERE id=?", (did,))
    await conn.commit()
    return cur.rowcount > 0


async def get_setting(conn: aiosqlite.Connection, key: str) -> str | None:
    cur = await conn.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    return row["value"] if row else None


async def set_setting(conn: aiosqlite.Connection, key: str, value: str) -> None:
    await conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    await conn.commit()
