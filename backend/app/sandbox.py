"""Safe workspace sandbox for generated project files.

All writes go under WORKSPACE_ROOT/projects/<project_id>/ — paths are resolved
and checked to prevent traversal (.., absolute paths, symlink escapes).
Generated code never receives server env/secrets; previews are served from a
separate /p/{id}/ route with no auth data in it.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from io import BytesIO
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent / "workspace_data"
PROJECTS_ROOT = WORKSPACE_ROOT / "projects"

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/@-]{0,255}$")
MAX_FILE_BYTES = 512 * 1024


class UnsafePathError(ValueError):
    pass


def valid_project_id(pid: str) -> str:
    if not _NAME_RE.match(pid):
        raise UnsafePathError("Invalid project id")
    return pid


def project_dir(pid: str) -> Path:
    d = (PROJECTS_ROOT / valid_project_id(pid)).resolve()
    if not str(d).startswith(str(PROJECTS_ROOT.resolve())):
        raise UnsafePathError("Project path escapes workspace")
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_join(pid: str, rel: str) -> Path:
    """Resolve rel inside the project dir; reject traversal/absolute paths."""
    if not _SAFE_PATH.match(rel) or ".." in rel.split("/") or rel.startswith("/"):
        raise UnsafePathError(f"Unsafe file path: {rel!r}")
    base = project_dir(pid)
    target = (base / rel).resolve()
    if not str(target).startswith(str(base.resolve()) + "/"):
        raise UnsafePathError(f"Path escapes project workspace: {rel!r}")
    return target


def write_file(pid: str, rel: str, content: str) -> Path:
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise UnsafePathError("File too large")
    p = safe_join(pid, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def read_file(pid: str, rel: str) -> str:
    p = safe_join(pid, rel)
    if not p.is_file():
        raise FileNotFoundError(rel)
    return p.read_text(encoding="utf-8", errors="replace")


def list_files(pid: str) -> list[dict]:
    base = project_dir(pid)
    out = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            out.append({
                "path": str(p.relative_to(base)),
                "size": p.stat().st_size,
            })
    return out


def delete_project_dir(pid: str) -> None:
    shutil.rmtree(project_dir(pid), ignore_errors=True)


def zip_project(pid: str) -> bytes:
    base = project_dir(pid)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(base.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(base))
    return buf.getvalue()
