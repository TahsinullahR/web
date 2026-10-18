"""Agent engine: planner, task queue, execution with approvals & cancellation.

Lifecycle: queued -> planning -> awaiting_approval (if any step needs it) ->
running -> completed | failed | cancelled.

Planning uses OpenRouter tool-style JSON prompting when a key is configured;
otherwise it falls back to a deterministic keyword planner so the platform
stays usable without paid/any API keys (free-first policy). The executor only
runs registered backend tools — never model-authored shell commands.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Optional

import aiosqlite

from .. import repo
from ..openrouter import OpenRouterAdapter, ProviderError
from .tools import ToolRegistry


class TaskControl:
    def __init__(self) -> None:
        self.cancelled = False
        self.approval_event: Optional[asyncio.Event] = None
        self.approved: Optional[bool] = None      # decision for current wait
        self.decisions: dict[int, str] = {}       # step_id -> approved|rejected


CONTROLS: dict[str, TaskControl] = {}


def _new_id() -> str:
    return uuid.uuid4().hex


PLAN_PROMPT = """You are NOVA AI's task planner. Break the user instruction into an ordered plan of steps.
Available tools (backend-validated, no shell access):
{tools}

Reply ONLY with a JSON array (no prose, no markdown fences). Each element:
{{"description": str, "tool": one of {tool_names} or "none", "args": {{}}, "needs_approval": bool}}
Mark needs_approval=true for destructive/consequential actions only."""

FALLBACK_RULES = [
    (re.compile(r"\b(website|site|landing|business page|webpage|সাইট|ওয়েব)", re.I),
     [("Create website project files", "project_create", lambda ins, pid_box: {"name": _slug(ins), "prompt": ins}),
      ("Verify generated files & asset references", "project_validate", lambda ins, pid_box: {"project_id": pid_box.get("id", "")})]),
    (re.compile(r"\b(delete|remove|মুছে|ডিলিট)", re.I),
     [("Delete requested project (destructive)", "project_delete", lambda ins, pid_box: {"project_id": pid_box.get("id", "")})]),
]


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9 -]", "", text.lower())[:40].strip().replace(" ", "-")
    return s or "generated-site"


def fallback_plan(instruction: str) -> list[dict]:
    steps = []
    pid_box: dict = {}
    for rx, rules in FALLBACK_RULES:
        if rx.search(instruction):
            for desc, tool, argf in rules:
                args = argf(instruction, pid_box)
                needs_appr = tool == "project_delete"
                steps.append({"description": desc, "tool": tool,
                              "args": args, "needs_approval": needs_appr})
            break
    if not steps:
        steps.append({"description": "Analyze request and reply with guidance (no tool needed)",
                      "tool": "none", "args": {}, "needs_approval": False})
    # Remember created project id for later steps via placeholder substitution
    return steps


async def make_plan(adapter: OpenRouterAdapter, registry: ToolRegistry,
                    instruction: str, log) -> list[dict]:
    if adapter.s.openrouter_api_key:
        try:
            msgs = [{"role": "system", "content": PLAN_PROMPT.format(
                         tools=registry.describe_for_prompt(),
                         tool_names=json.dumps(registry.names()))},
                     {"role": "user", "content": instruction[:4000]}]
            text = ""
            async for delta in adapter.stream_chat(msgs, adapter.s.openrouter_model):
                text += delta
            m = re.search(r"\[[\s\S]*\]", text)
            if m:
                parsed = json.loads(m.group(0))
                steps = []
                for st in parsed[:12]:
                    tool = st.get("tool", "none")
                    if tool != "none" and registry.get(tool) is None:
                        log(f"Planner suggested unknown tool '{tool}' — step kept as manual note")
                        tool, st["needs_approval"] = "none", False
                    steps.append({"description": str(st.get("description", ""))[:500],
                                  "tool": tool,
                                  "args": st.get("args") or {},
                                  "needs_approval": bool(st.get("needs_approval"))})
                if steps:
                    return steps
            log("Model plan was not valid JSON — using deterministic planner.")
        except (ProviderError, json.JSONDecodeError, asyncio.TimeoutError) as e:
            log(f"LLM planning unavailable ({getattr(e, 'code', type(e).__name__)}) — using deterministic planner.")
    return fallback_plan(instruction)


async def run_task(conn: aiosqlite.Connection, registry: ToolRegistry, task_id: str,
                   auto_approve: bool = False, approved_step_ids: set[int] | None = None,
                   retried: bool = False) -> None:
    """Execute a persisted task. Safe to run as a background asyncio task."""
    ctrl = CONTROLS.setdefault(task_id, TaskControl())
    task = await repo.get_task(conn, task_id)
    if not task:
        return
    if not retried and task["status"] not in ("queued", "awaiting_approval"):
        return
    if retried:
        # reset failed/skipped steps for another pass
        for st in await repo.get_steps(conn, task_id):
            if st["status"] in ("failed", "skipped"):
                await repo.set_step_status(conn, st["id"], "pending")

    def log(msg: str, level: str = "info") -> None:
        asyncio.create_task(repo.add_log(conn, task_id, level, msg))

    steps = await repo.get_steps(conn, task_id)
    await repo.set_task_status(conn, task_id, "running")
    log(f"Task started: {task['instruction'][:120]}")

    summary_parts: list[str] = []
    failures: list[str] = []
    created_project_id = ""

    for st in steps:
        if ctrl.cancelled:
            await repo.set_step_status(conn, st["id"], "skipped")
            await repo.set_task_status(conn, task_id, "cancelled")
            log("Cancelled by user.", "warn")
            CONTROLS.pop(task_id, None)
            return

        already = ctrl.decisions.get(st["id"])
        if st["needs_approval"] and not auto_approve and already is None:
            await repo.set_step_status(conn, st["id"], "pending")
            await repo.set_task_status(conn, task_id, "awaiting_approval")
            log(f"Step {st['idx'] + 1} '{st['description']}' requires your approval.", "warn")
            ctrl.approval_event = asyncio.Event()
            ctrl.approved = None
            try:
                await asyncio.wait_for(ctrl.approval_event.wait(), timeout=1800)
            except asyncio.TimeoutError:
                ctrl.decisions[st["id"]] = "rejected"
                await repo.set_step_status(conn, st["id"], "rejected")
                await repo.set_task_status(conn, task_id, "failed")
                log("Approval timed out; task failed.", "error")
                CONTROLS.pop(task_id, None)
                return
            ctrl.approval_event = None
            ctrl.decisions[st["id"]] = "approved" if ctrl.approved else "rejected"
            already = ctrl.decisions[st["id"]]
        if st["needs_approval"] and already == "rejected":
            await repo.set_step_status(conn, st["id"], "rejected")
            summary_parts.append(f"Step {st['idx'] + 1} rejected by user.")
            continue

        if st["tool"] == "none" or not st["tool"]:
            await repo.set_step_status(conn, st["id"], "done", output="Manual/analysis step — noted.")
            summary_parts.append(st["description"])
            continue

        await repo.set_step_status(conn, st["id"], "running")
        args = dict(st["args_json"] if isinstance(st["args_json"], dict) else json.loads(st["args_json"] or "{}"))
        # Late-bind project id placeholders from earlier create steps
        if "{project_id}" in json.dumps(args) and created_project_id:
            args = json.loads(json.dumps(args).replace("{project_id}", created_project_id))
        if not args.get("project_id") and created_project_id and st["tool"] in (
                "project_validate", "project_write_file", "project_delete"):
            args["project_id"] = created_project_id
        if st["tool"] == "project_create" and not args.get("prompt"):
            args["prompt"] = task["instruction"]

        log(f"Running tool {st['tool']} …")
        result = await registry.run(st["tool"], args)
        if result.get("ok"):
            await repo.set_step_status(conn, st["id"], "done",
                                       output=str(result.get("summary", result))[:2000])
            summary_parts.append(str(result.get("summary", "OK")))
            if result.get("project_id"):
                created_project_id = result["project_id"]
        else:
            err = str(result.get("error", "unknown error"))
            await repo.set_step_status(conn, st["id"], "failed", output=err)
            failures.append(f"Step {st['idx'] + 1}: {err}")
            log(f"Tool {st['tool']} failed: {err}", "error")

    if failures:
        await repo.set_task_status(conn, task_id, "failed",
                                   summary="; ".join(summary_parts)[:4000],
                                   error="; ".join(failures)[:2000])
        log("Task finished with failures.", "error")
    else:
        await repo.set_task_status(conn, task_id, "completed",
                                   summary="; ".join(summary_parts)[:4000] or "No steps executed.")
        log("Task completed successfully.")
    CONTROLS.pop(task_id, None)


async def create_task(conn: aiosqlite.Connection, registry: ToolRegistry,
                      adapter: OpenRouterAdapter, instruction: str) -> str:
    task_id = _new_id()
    plan = await make_plan(adapter, registry, instruction,
                           lambda m: asyncio.create_task(repo.add_log(conn, task_id, "info", m)))
    await repo.create_task(conn, task_id, instruction, plan)
    return task_id
