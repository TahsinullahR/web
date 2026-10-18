"""Agent task endpoints: create, list, detail, approve/reject, cancel."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from .. import repo
from ..agents import engine
from ..schemas import TaskCreate

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/tasks", status_code=201)
async def create_task(body: TaskCreate, request: Request):
    conn = request.app.state.conn
    registry = request.app.state.tools
    adapter = request.app.state.openrouter
    task_id = await engine.create_task(conn, registry, adapter, body.instruction)
    # Kick off execution in background; it will pause for approval if needed.
    _spawn(request, task_id, retried=False)
    return await repo.get_task(conn, task_id)


def _spawn(request: Request, task_id: str, retried: bool):
    bg = asyncio.create_task(engine.run_task(request.app.state.conn,
                                             request.app.state.tools,
                                             task_id, retried=retried))
    request.app.state.bg_tasks.add(bg)
    bg.add_done_callback(request.app.state.bg_tasks.discard)


@router.post("/tasks/{tid}/retry")
async def retry(tid: str, request: Request):
    task = await repo.get_task(request.app.state.conn, tid)
    if not task:
        raise HTTPException(404, "Task not found")
    if task["status"] not in ("failed", "cancelled"):
        raise HTTPException(409, f"Only failed/cancelled tasks can be retried (now {task['status']})")
    if tid in engine.CONTROLS and engine.CONTROLS[tid].approval_event:
        raise HTTPException(409, "Task is currently awaiting approval")
    await repo.set_task_status(request.app.state.conn, tid, "queued")
    engine.CONTROLS.pop(tid, None)
    _spawn(request, tid, retried=True)
    return await repo.get_task(request.app.state.conn, tid)


@router.get("/tasks")
async def list_tasks(request: Request, limit: int = 50, offset: int = 0):
    return await repo.list_tasks(request.app.state.conn, min(limit, 200), max(offset, 0))


@router.get("/tasks/{tid}")
async def get_task(tid: str, request: Request):
    task = await repo.get_task(request.app.state.conn, tid)
    if not task:
        raise HTTPException(404, "Task not found")
    steps = await repo.get_steps(request.app.state.conn, tid)
    logs = await repo.get_logs(request.app.state.conn, tid)
    return {"task": task, "steps": steps, "logs": logs}


@router.post("/tasks/{tid}/approve")
async def approve(tid: str, request: Request, body: dict | None = None):
    approved = bool((body or {}).get("approved", True))
    ctrl = engine.CONTROLS.get(tid)
    if not ctrl or ctrl.approval_event is None:
        raise HTTPException(409, "Task is not awaiting approval")
    ctrl.approved = approved
    ctrl.approval_event.set()
    return {"ok": True, "approved": approved}


@router.post("/tasks/{tid}/cancel")
async def cancel(tid: str, request: Request):
    task = await repo.get_task(request.app.state.conn, tid)
    if not task:
        raise HTTPException(404, "Task not found")
    ctrl = engine.CONTROLS.get(tid)
    if ctrl:
        ctrl.cancelled = True
        if ctrl.approval_event:
            ctrl.approved = False
            ctrl.approval_event.set()
        return {"ok": True, "cancelling": True}
    # Not running (already finished?) — only allow marking queued tasks cancelled.
    if task["status"] in ("completed", "failed", "cancelled"):
        raise HTTPException(409, f"Task already {task['status']}")
    await repo.set_task_status(request.app.state.conn, tid, "cancelled")
    return {"ok": True, "cancelled": True}
