"""Pydantic request/response models for the NOVA AI API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ConversationCreate(BaseModel):
    title: str = Field(default="New chat", max_length=200)
    model: str = Field(default="", max_length=200)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)


class ConversationOut(BaseModel):
    id: str
    title: str
    model: str
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant", "system"]
    content: str
    model: str
    created_at: str


class ChatRequest(BaseModel):
    # conversation_id arrives via the path; kept optional in the body.
    conversation_id: str = ""
    content: str = Field(min_length=1, max_length=32_000)
    model: str | None = Field(default=None, max_length=200)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be blank")
        return v


class HealthOut(BaseModel):
    status: str
    provider_configured: bool
    default_model: str
    db: str
    version: str
