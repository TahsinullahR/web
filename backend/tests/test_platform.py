"""Tests for Phase 2/3 features: agent lifecycle, projects, sandbox safety,
media honesty, devices/settings. All provider calls are mocked — no network."""
from __future__ import annotations

import asyncio
import io
import json
import zipfile


async def _wait_task(client, tid, target=("completed", "failed", "cancelled"), timeout=5.0):
    """Poll task status until terminal state (bg tasks run in same event loop)."""
    for _ in range(int(timeout / 0.05)):
        r = await client.get(f"/api/agent/tasks/{tid}")
        st = r.json()["task"]["status"]
        if st in target:
            return r.json()
        await asyncio.sleep(0.05)
    raise AssertionError(f"task {tid} did not finish; last status {st}")


# ---------- Sandbox path safety ----------

def test_sandbox_rejects_traversal(tmp_path, monkeypatch):
    import app.sandbox as sb
    monkeypatch.setattr(sb, "WORKSPACE_ROOT", tmp_path / "ws")
    monkeypatch.setattr(sb, "PROJECTS_ROOT", tmp_path / "ws" / "projects")
    pid = "abc123"
    for bad in ("../evil.txt", "/etc/passwd", "a/../../b", ".."):
        try:
            sb.safe_join(pid, bad)
            assert False, f"should reject {bad}"
        except sb.UnsafePathError:
            pass
    ok = sb.safe_join(pid, "css/style.css")
    assert str(ok).startswith(str((tmp_path / "ws" / "projects" / pid).resolve()))


# ---------- Agent lifecycle ----------

async def test_agent_website_task_completes_with_verified_output(client):
    r = await client.post("/api/agent/tasks",
                          json={"instruction": "Create a responsive business website and test it"})
    assert r.status_code == 201
    tid = r.json()["id"]
    detail = await _wait_task(client, tid)
    assert detail["task"]["status"] == "completed", detail["task"]
    steps = detail["steps"]
    assert [s["tool"] for s in steps] == ["project_create", "project_validate"]
    assert all(s["status"] == "done" for s in steps)
    # Real verification: project files exist on disk & validate passed
    proj_id = json.loads(steps[0]["output"]) if steps[0]["output"].startswith("{") else None
    assert "Created project" in steps[0]["output"]
    pid = steps[0]["output"].split("(")[1].split(")")[0]
    pr = await client.get(f"/p/{pid}/index.html")
    assert pr.status_code == 200 and "<html" in pr.text.lower()
    assert len(detail["logs"]) >= 3


async def test_agent_delete_requires_approval_then_reject(client):
    r = await client.post("/api/agent/tasks",
                          json={"instruction": "delete the project"})
    tid = r.json()["id"]
    # Should park in awaiting_approval
    for _ in range(40):
        d = (await client.get(f"/api/agent/tasks/{tid}")).json()
        if d["task"]["status"] == "awaiting_approval":
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError(f"expected awaiting_approval, got {d['task']['status']}")
    step = next(s for s in d["steps"] if s["needs_approval"])
    # Reject it
    ar = await client.post(f"/api/agent/tasks/{tid}/approve", json={"approved": False})
    assert ar.status_code == 200
    final = await _wait_task(client, tid)
    del_step = next(s for s in final["steps"] if s["needs_approval"])
    assert del_step["status"] == "rejected"
    assert final["task"]["status"] == "completed"  # rejection handled gracefully


async def test_agent_cancel(client):
    r = await client.post("/api/agent/tasks", json={"instruction": "make a site please"})
    tid = r.json()["id"]
    c = await client.post(f"/api/agent/tasks/{tid}/cancel")
    assert c.status_code == 200
    # Either already finished or becomes cancelled — both are honest outcomes
    for _ in range(60):
        st = (await client.get(f"/api/agent/tasks/{tid}")).json()["task"]["status"]
        if st in ("cancelled", "completed"):
            assert st in ("cancelled", "completed")
            return
        await asyncio.sleep(0.05)
    raise AssertionError("no terminal status")


async def test_agent_retry_endpoint_guards(client):
    r = await client.post("/api/agent/tasks", json={"instruction": "hello world task"})
    tid = r.json()["id"]
    await _wait_task(client, tid)
    rr = await client.post(f"/api/agent/tasks/{tid}/retry")
    assert rr.status_code == 409  # completed tasks can't retry


# ---------- Projects / builder ----------

async def test_project_crud_files_versions_restore_export(client):
    r = await client.post("/api/projects", json={"name": "demo-site", "kind": "static",
                                                 "prompt": "test"})
    assert r.status_code == 201
    pid = r.json()["id"]
    assert r.json()["status"] == "ready"

    # list & detail
    assert pid in [p["id"] for p in (await client.get("/api/projects")).json()]
    detail = (await client.get(f"/api/projects/{pid}")).json()
    paths = [f["path"] for f in detail["files"]]
    assert {"index.html", "style.css", "script.js"} <= set(paths)

    # edit file -> version bump
    r = await client.put(f"/api/projects/{pid}/files",
                         json={"path": "index.html", "content": "<html><body>v2</body></html>"})
    assert r.json()["version"] == 2
    # missing asset now fails validation
    v = await client.post(f"/api/projects/{pid}/validate")
    assert v.json()["ok"] is False

    # restore v1
    versions = (await client.get(f"/api/projects/{pid}/versions/index.html")).json()
    assert len(versions) == 2
    rs = await client.post(f"/api/projects/{pid}/restore",
                           json={"path": "index.html", "version": 1})
    assert rs.json()["restored_version"] == 3
    v2 = await client.post(f"/api/projects/{pid}/validate")
    assert v2.json()["ok"] is True

    # preview serves real file with CSP isolation header
    pv = await client.get(f"/p/{pid}/index.html")
    assert pv.status_code == 200 and "Content-Security-Policy" in pv.headers

    # zip export contains files
    ex = await client.get(f"/api/projects/{pid}/export")
    assert ex.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(ex.content))
    assert "index.html" in z.namelist()

    # unsafe path rejected
    bad = await client.put(f"/api/projects/{pid}/files",
                           json={"path": "../escape.txt", "content": "x"})
    assert bad.status_code == 400

    # delete
    assert (await client.delete(f"/api/projects/{pid}")).status_code == 204
    assert (await client.get(f"/api/projects/{pid}")).status_code == 404


async def test_generate_without_key_returns_real_error_not_fake(client):
    r = await client.post("/api/projects", json={"name": "gen-test"})
    pid = r.json()["id"]
    # conftest sets a fake key; adapter will attempt network -> in tests we mock
    import app.openrouter as ormod
    async def boom(self, messages, model):
        raise ormod.ProviderError("not_configured", "OPENROUTER_API_KEY is not set on the server.")
        yield ""
    orig = ormod.OpenRouterAdapter.stream_chat
    ormod.OpenRouterAdapter.stream_chat = boom
    try:
        g = await client.post(f"/api/projects/{pid}/generate",
                              json={"instruction": "add a contact form"})
        assert g.status_code == 502
        body = g.json()
        assert body["error"]["code"] == "not_configured"
        proj = (await client.get(f"/api/projects/{pid}")).json()
        assert proj["status"] == "error"  # honest failure state persisted
    finally:
        ormod.OpenRouterAdapter.stream_chat = orig


async def test_generate_applies_structured_file_changes(client):
    r = await client.post("/api/projects", json={"name": "edit-test"})
    pid = r.json()["id"]
    import app.openrouter as ormod
    async def fake(self, messages, model):
        for chunk in ['```html=path=index.html\n<!DOCTYPE html><html><head>',
                      '<link rel="stylesheet" href="style.css"></head><body>Contact OK',
                      '<script src="script.js"></script></body></html>\n```']:
            yield chunk
    orig = ormod.OpenRouterAdapter.stream_chat
    ormod.OpenRouterAdapter.stream_chat = fake
    try:
        g = await client.post(f"/api/projects/{pid}/generate",
                              json={"instruction": "make a contact page"})
        assert g.status_code == 200
        body = g.json()
        assert body["ok"] is True
        assert body["applied"][0]["path"] == "index.html"
        content = (await client.get(f"/api/projects/{pid}/files/index.html")).json()["content"]
        assert "Contact OK" in content
    finally:
        ormod.OpenRouterAdapter.stream_chat = orig


# ---------- Creative studio honesty ----------

async def test_media_providers_report_honest_status(client):
    provs = (await client.get("/api/media/providers")).json()["providers"]
    by_kind = {p["kind"]: p for p in provs}
    assert by_kind["image"]["connected"] is True          # pollinations: real
    assert by_kind["tts"]["connected"] is False           # not connected
    assert by_kind["video"]["connected"] is False
    assert "requires" in by_kind["tts"]


async def test_media_video_unavailable_503_and_logged(client):
    r = await client.post("/api/media/generate", json={"kind": "video", "prompt": "cat dance"})
    assert r.status_code == 503
    jobs = (await client.get("/api/media/jobs")).json()
    assert jobs[0]["status"] == "unavailable"
    assert "provider" in jobs[0]["detail"].lower() or "free" in jobs[0]["detail"].lower()


async def test_media_image_success_saved_to_assets(client):
    import httpx
    orig_get = httpx.AsyncClient.get
    async def fake_get(self, url, **kw):
        resp = httpx.Response(200, headers={"content-type": "image/jpeg"},
                              content=b"\xff\xd8\xff\xe0FAKEJPEG")
        resp.request = httpx.Request("GET", url)
        return resp
    httpx.AsyncClient.get = fake_get
    try:
        r = await client.post("/api/media/generate",
                              json={"kind": "image", "prompt": "a nova star"})
        assert r.status_code == 200
        job = r.json()
        assert job["status"] == "completed"
        a = await client.get(f"/api/media/assets/{job['asset_path']}")
        assert a.status_code == 200 and a.content.startswith(b"\xff\xd8")
    finally:
        httpx.AsyncClient.get = orig_get


# ---------- Devices / settings / data ----------

async def test_device_register_list_delete(client):
    d = (await client.post("/api/devices",
                           json={"name": "My Laptop", "platform": "windows", "paired": True})).json()
    assert d["paired"] == 1
    lst = (await client.get("/api/devices")).json()
    assert any(x["id"] == d["id"] for x in lst)
    assert (await client.delete(f"/api/devices/{d['id']}")).status_code == 204


async def test_settings_roundtrip_no_secret_leak(client):
    r = await client.put("/api/settings", json={"language": "bn", "theme": "dark"})
    assert r.status_code == 200
    s = (await client.get("/api/settings")).json()
    assert s["language"] == "bn"
    assert s["provider"]["openrouter_configured"] is True
    assert "test-key-not-real" not in json.dumps(s)  # key never exposed


async def test_data_export_and_guarded_delete(client):
    c = (await client.post("/api/conversations", json={"title": "exp"})).json()
    e = (await client.post("/api/data/export")).json()
    assert any(x["id"] == c["id"] for x in e["conversations"])
    bad = await client.post("/api/data/delete-all", json={})
    assert bad.status_code == 400  # requires explicit confirm
    ok = await client.post("/api/data/delete-all", json={"confirm": "DELETE"})
    assert ok.status_code == 200
    assert (await client.get("/api/conversations")).json() == []
