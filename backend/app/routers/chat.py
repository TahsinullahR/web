"""Chat endpoint: streams OpenRouter responses over SSE and persists them.

Client-side stop = closing the EventSource; whatever was received is saved.
Errors are sent as an SSE `error` event with a structured code so the UI can
display real provider failures (never fakes them).
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .. import repo
from ..openrouter import OpenRouterAdapter, ProviderError
from ..schemas import ChatRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/{conversation_id}/stream")
async def stream_chat(conversation_id: str, body: ChatRequest, request: Request):
    conn = request.app.state.conn
    adapter: OpenRouterAdapter = request.app.state.openrouter

    convo = await repo.get_conversation(conn, conversation_id)
    if not convo:
        raise HTTPException(404, "Conversation not found")

    model = (body.model or convo["model"] or adapter.s.openrouter_model).strip()

    # Persist the user turn first.
    await repo.add_message(conn, conversation_id, "user", body.content.strip(), model)

    # Auto-title new conversations from the first message.
    if convo["title"] == "New chat":
        title = body.content.strip().splitlines()[0][:60]
        await repo.update_conversation(conn, conversation_id, title=title, model=model)

    history = await repo.get_messages(conn, conversation_id)
    payload_msgs = [{"role": m["role"], "content": m["content"]}
                    for m in history if m["role"] in ("user", "assistant")]

    async def event_gen():
        collected: list[str] = []
        try:
            async for delta in adapter.stream_chat(payload_msgs, model):
                collected.append(delta)
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            text = "".join(collected)
            if text:
                msg = await repo.add_message(conn, conversation_id, "assistant",
                                             text, model)
                yield f"data: {json.dumps({'done': True, 'message_id': msg['id']})}\n\n"
            else:
                yield f"data: {json.dumps({'error': {'code': 'empty_response', 'message': 'The model returned no content. Try another model.'}})}\n\n"
        except ProviderError as e:
            # Save partial output if we got any before failing.
            partial = "".join(collected)
            if partial:
                await repo.add_message(conn, conversation_id, "assistant", partial, model)
            yield f"data: {json.dumps(e.to_dict())}\n\n"
        except asyncio.CancelledError:
            partial = "".join(collected)
            if partial:
                await repo.add_message(conn, conversation_id, "assistant", partial, model)
            raise

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{conversation_id}/regenerate")
async def regenerate(conversation_id: str, request: Request,
                    body: dict | None = None):
    """Remove the last assistant reply; client then re-streams the last question."""
    conn = request.app.state.conn
    if not await repo.get_conversation(conn, conversation_id):
        raise HTTPException(404, "Conversation not found")
    last = await repo.latest_assistant_message(conn, conversation_id)
    if not last:
        raise HTTPException(409, "Nothing to regenerate")
    await repo.delete_latest_assistant(conn, conversation_id)
    return {"deleted_message_id": last["id"]}
