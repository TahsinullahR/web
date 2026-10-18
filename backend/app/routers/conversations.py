"""Conversation & message CRUD endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import repo
from ..schemas import (ConversationCreate, ConversationOut, ConversationUpdate,
                       MessageOut)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _conn(request: Request):
    return request.app.state.conn


@router.get("", response_model=list[ConversationOut])
async def list_conversations(request: Request,
                             limit: int = Query(default=100, ge=1, le=500),
                             offset: int = Query(default=0, ge=0)):
    return await repo.list_conversations(_conn(request), limit, offset)


@router.post("", response_model=ConversationOut, status_code=201)
async def create_conversation(body: ConversationCreate, request: Request):
    return await repo.create_conversation(_conn(request), body.title.strip() or "New chat",
                                          body.model)


@router.get("/{cid}", response_model=ConversationOut)
async def get_conversation(cid: str, request: Request):
    convo = await repo.get_conversation(_conn(request), cid)
    if not convo:
        raise HTTPException(404, "Conversation not found")
    return convo


@router.get("/{cid}/messages", response_model=list[MessageOut])
async def get_messages(cid: str, request: Request):
    if not await repo.get_conversation(_conn(request), cid):
        raise HTTPException(404, "Conversation not found")
    return await repo.get_messages(_conn(request), cid)


@router.patch("/{cid}", response_model=ConversationOut)
async def update_conversation(cid: str, body: ConversationUpdate, request: Request):
    convo = await repo.update_conversation(
        _conn(request), cid,
        title=body.title.strip() if body.title and body.title.strip() else None,
        model=body.model,
    )
    if not convo:
        raise HTTPException(404, "Conversation not found")
    return convo


@router.delete("/{cid}", status_code=204)
async def delete_conversation(cid: str, request: Request):
    if not await repo.delete_conversation(_conn(request), cid):
        raise HTTPException(404, "Conversation not found")
