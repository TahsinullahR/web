"""Website builder: projects, files (with version history), AI generation/edits,
safe preview serving, ZIP export.

Preview: files are served from /p/{pid}/ with CSP that blocks external origins
and frame-ancestors localhost — generated code is isolated from platform data.
No secrets are ever injected into generated files or served previews.
"""
from __future__ import annotations

import json
import re
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .. import repo
from ..openrouter import ProviderError
from ..sandbox import (UnsafePathError, project_dir, read_file, safe_join,
                       write_file, zip_project)
from ..schemas import (ProjectCreate, ProjectFileIn, ProjectGenerateIn,
                       ProjectPatchIn, ProjectRestoreIn)

router = APIRouter(prefix="/api/projects", tags=["projects"])
preview_router = APIRouter(tags=["preview"])


async def create_project_record(conn, name: str, kind: str, prompt: str,
                                files: dict[str, str]) -> str:
    pid = uuid.uuid4().hex
    await repo.create_project(conn, pid, name[:64], kind, prompt[:4000])
    for path, content in files.items():
        try:
            write_file(pid, path, content)
            await repo.save_project_file(conn, pid, path, content)
        except UnsafePathError:
            await repo.update_project_status(conn, pid, "error",
                                            f"Rejected unsafe path: {path}")
    return pid


def _extract_files(text: str) -> dict[str, str]:
    """Parse ```lang=path fenced blocks into {path: content}. Tolerant parser."""
    out: dict[str, str] = {}
    for m in re.finditer(
            r"```[a-zA-Z0-9]*[^\n`]*?path=(['\"]?)([\w./@ -]+)\1[^\n]*\n([\s\S]*?)```", text):
        path = m.group(3).strip().lstrip("/")
        if path and len(out) < 20:
            out[path] = m.group(4)
    if not out:
        # fallback: bare fences with a filename comment on first line
        for m in re.finditer(r"```[a-zA-Z0-9]*\n// ?file: ([\w./-]+)\n([\s\S]*?)```", text):
            out[m.group(1)] = m.group(2)
    return out


VALIDATION_HINTS = ("index.html missing", "no <html> tag", "referenced file missing")


@router.post("", status_code=201)
async def create_project(body: ProjectCreate, request: Request):
    conn = request.app.state.conn
    from ..agents.tools import _starter_files
    pid = await create_project_record(conn, body.name, body.kind, body.prompt,
                                      _starter_files(body.name))
    await repo.update_project_status(conn, pid, "ready")
    return await repo.get_project(conn, pid)


@router.get("")
async def list_projects(request: Request):
    return await repo.list_projects(request.app.state.conn)


@router.get("/{pid}")
async def get_project(pid: str, request: Request):
    proj = await repo.get_project(request.app.state.conn, pid)
    if not proj:
        raise HTTPException(404, "Project not found")
    files = await repo.current_files(request.app.state.conn, pid)
    proj["files"] = [{k: v for k, v in f.items() if k != "content"} | {"size": len(f["content"])}
                     for f in files]
    return proj


@router.get("/{pid}/files/{path:path}")
async def get_file(pid: str, path: str, request: Request):
    if not await repo.get_project(request.app.state.conn, pid):
        raise HTTPException(404, "Project not found")
    try:
        content = read_file(pid, path)
    except UnsafePathError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "File not found")
    return {"path": path, "content": content}


@router.put("/{pid}/files")
async def put_file(pid: str, body: ProjectFileIn, request: Request):
    conn = request.app.state.conn
    if not await repo.get_project(conn, pid):
        raise HTTPException(404, "Project not found")
    try:
        write_file(pid, body.path, body.content)
    except UnsafePathError as e:
        raise HTTPException(400, str(e))
    version = await repo.save_project_file(conn, pid, body.path, body.content)
    return {"ok": True, "version": version}


@router.get("/{pid}/versions/{path:path}")
async def versions(pid: str, path: str, request: Request):
    if not await repo.get_project(request.app.state.conn, pid):
        raise HTTPException(404, "Project not found")
    return await repo.file_versions(request.app.state.conn, pid, path)


@router.post("/{pid}/restore")
async def restore(pid: str, body: ProjectRestoreIn, request: Request):
    conn = request.app.state.conn
    if not await repo.get_project(conn, pid):
        raise HTTPException(404, "Project not found")
    content = await repo.get_file_version(conn, pid, body.path, body.version)
    if content is None:
        raise HTTPException(404, "Version not found")
    try:
        write_file(pid, body.path, content)
    except UnsafePathError as e:
        raise HTTPException(400, str(e))
    v = await repo.save_project_file(conn, pid, body.path, content)
    return {"ok": True, "restored_version": v}


@router.patch("/{pid}")
async def patch_project(pid: str, body: ProjectPatchIn, request: Request):
    conn = request.app.state.conn
    if not await repo.get_project(conn, pid):
        raise HTTPException(404, "Project not found")
    if body.name:
        await repo.rename_project(conn, pid, body.name.strip()[:64])
    return await repo.get_project(conn, pid)


@router.delete("/{pid}", status_code=204)
async def delete_project(pid: str, request: Request):
    conn = request.app.state.conn
    if not await repo.delete_project(conn, pid):
        raise HTTPException(404, "Project not found")
    from ..sandbox import delete_project_dir
    delete_project_dir(pid)


@router.get("/{pid}/export")
async def export(pid: str, request: Request):
    if not await repo.get_project(request.app.state.conn, pid):
        raise HTTPException(404, "Project not found")
    data = zip_project(pid)
    return Response(content=data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="project-{pid}.zip"'})


@router.post("/{pid}/validate")
async def validate(pid: str, request: Request):
    conn = request.app.state.conn
    if not await repo.get_project(conn, pid):
        raise HTTPException(404, "Project not found")
    from ..agents.tools import _tool_project_validate
    res = await _tool_project_validate({"project_id": pid})
    await repo.update_project_status(conn, pid, "ready" if res["ok"] else "error",
                                     res.get("summary") or res.get("error"))
    return res


@router.post("/{pid}/generate")
async def generate(pid: str, body: ProjectGenerateIn, request: Request):
    """AI-generate or AI-modify project files via OpenRouter (server-side).

    Returns structured file changes; applies them only when the model output
    parses safely. On provider failure returns a real error — never fake files.
    """
    conn = request.app.state.conn
    adapter = request.app.state.openrouter
    proj = await repo.get_project(conn, pid)
    if not proj:
        raise HTTPException(404, "Project not found")

    current = await repo.current_files(conn, pid)
    ctx = "\n\n".join(f"=== FILE: {f['path']} ===\n{f['content'][:6000]}" for f in current[:8])
    system = (
        "You are NOVA AI's website builder. Produce complete file contents. "
        "For each file output a fenced block whose opening line contains path=<relative/path>, "
        "e.g. ```html=path=index.html. Only modify files the user asked about plus any needed "
        "supporting files. No commentary outside code blocks. Do not include secrets or env vars."
    )
    user = f"Instruction: {body.instruction}\n\nCurrent files:\n{ctx}" if body.instruction else ""
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    await repo.update_project_status(conn, pid, "building")
    try:
        text = ""
        async for delta in adapter.stream_chat(msgs, body.model or adapter.s.openrouter_model):
            text += delta
    except ProviderError as e:
        await repo.update_project_status(conn, pid, "error", e.code)
        return JSONResponse(e.to_dict(), status_code=e.status or 502)

    files = _extract_files(text)
    if not files:
        await repo.update_project_status(conn, pid, "error", "Model returned no parseable file blocks")
        raise HTTPException(502, "The model did not return valid file blocks. Try again or edit manually.")

    applied = []
    rejected = []
    for path, content in files.items():
        try:
            write_file(pid, path, content)
            v = await repo.save_project_file(conn, pid, path, content)
            applied.append({"path": path, "version": v})
        except UnsafePathError as e:
            rejected.append(str(e))

    val = await _tool_project_validate({"project_id": pid})
    status = "ready" if val["ok"] else "error"
    await repo.update_project_status(conn, pid, status, json.dumps(val)[:2000])
    return {"ok": val["ok"], "applied": applied, "rejected": rejected,
            "validation": val, "status": status}


# ---------- Preview server route (isolated static serving) ----------

@preview_router.get("/p/{pid}/{path:path}")
async def preview(pid: str, path: str, request: Request):
    base = project_dir(pid)  # validates pid
    rel = path or "index.html"
    try:
        target = safe_join(pid, rel)
    except UnsafePathError:
        raise HTTPException(400, "Unsafe path")
    if not target.is_file():
        # SPA-ish fallback to index.html so relative links work
        idx = base / "index.html"
        if idx.is_file():
            target = idx
        else:
            raise HTTPException(404)
    resp = FileResponse(target)
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'none'; frame-ancestors 'self' http://localhost:3000"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
