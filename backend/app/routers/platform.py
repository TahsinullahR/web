"""Devices (Windows agent / Android companion pairing status) + settings endpoints.

Honest state: no desktop agent can pair until it is actually installed and
connects with a pairing token; these endpoints only reflect real registrations.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from .. import repo
from ..schemas import DeviceIn, SettingsIn

router = APIRouter(prefix="/api", tags=["devices-settings"])


@router.get("/devices")
async def list_devices(request: Request):
    return await repo.list_devices(request.app.state.conn)


@router.post("/devices")
async def register_device(body: DeviceIn, request: Request):
    """Register/update a device heartbeat. Real agents call this to appear."""
    did = body.id or uuid.uuid4().hex
    dev = await repo.upsert_device(request.app.state.conn, did, body.name[:64],
                                   body.platform, bool(body.paired))
    return dev


@router.delete("/devices/{did}", status_code=204)
async def remove_device(did: str, request: Request):
    if not await repo.delete_device(request.app.state.conn, did):
        raise HTTPException(404, "Device not found")


@router.get("/settings")
async def get_settings(request: Request):
    conn = request.app.state.conn
    out = {}
    for key in ("language", "default_model", "theme"):
        v = await repo.get_setting(conn, key)
        if v is not None:
            out[key] = v
    # Provider status never includes the key itself.
    s = request.app.state.settings
    out["provider"] = {
        "openrouter_configured": bool(s.openrouter_api_key),
        "base_url": s.openrouter_base_url,
        "default_model": s.openrouter_model,
    }
    return out


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request):
    conn = request.app.state.conn
    for k, v in body.model_dump(exclude_none=True).items():
        if v is not None:
            await repo.set_setting(conn, k, str(v))
    return {"ok": True}


@router.post("/data/export")
async def export_data(request: Request):
    """Export all user data (conversations, messages, projects metadata, tasks)."""
    conn = request.app.state.conn
    data = {
        "conversations": await repo.list_conversations(conn, 500, 0),
        "projects": await repo.list_projects(conn),
        "tasks": await repo.list_tasks(conn, 200, 0),
        "media_jobs": await repo.list_media_jobs(conn, 200),
    }
    for c in data["conversations"]:
        c["messages"] = await repo.get_messages(conn, c["id"])
    return data


@router.post("/data/delete-all")
async def delete_all(request: Request, body: dict | None = None):
    """Requires explicit confirm text 'DELETE' — protects against accidental wipes."""
    if (body or {}).get("confirm") != "DELETE":
        raise HTTPException(400, "Pass {\"confirm\": \"DELETE\"} to wipe all local data")
    conn = request.app.state.conn
    for table in ("task_logs", "task_steps", "agent_tasks", "project_files",
                  "projects", "media_jobs", "devices", "settings", "messages",
                  "conversations"):
        await conn.execute(f"DELETE FROM {table}")
    await conn.commit()
    from ..sandbox import PROJECTS_ROOT
    from ..agents.tools import registry  # noqa: F401
    import shutil
    if PROJECTS_ROOT.exists():
        shutil.rmtree(PROJECTS_ROOT)
        PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    return {"ok": True}
