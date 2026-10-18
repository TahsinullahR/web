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


# ---------- Agent ----------

class TaskCreate(BaseModel):
    instruction: str = Field(min_length=3, max_length=4000)

    @field_validator("instruction")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("instruction must not be blank")
        return v


# ---------- Projects ----------

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: Literal["static", "react"] = "static"
    prompt: str = Field(default="", max_length=4000)


class ProjectFileIn(BaseModel):
    path: str = Field(min_length=1, max_length=256)
    content: str = Field(default="", max_length=512_000)


class ProjectGenerateIn(BaseModel):
    instruction: str = Field(min_length=3, max_length=4000)
    model: str | None = Field(default=None, max_length=200)


class ProjectPatchIn(BaseModel):
    name: str | None = Field(default=None, max_length=64)


class ProjectRestoreIn(BaseModel):
    path: str = Field(min_length=1, max_length=256)
    version: int = Field(ge=1)


# ---------- Creative studio ----------

class MediaGenerateIn(BaseModel):
    kind: Literal["image", "video", "audio", "tts", "stt"]
    prompt: str = Field(min_length=1, max_length=1000)

    @field_validator("prompt")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("prompt must not be blank")
        return v


# ---------- Devices & settings ----------

class DeviceIn(BaseModel):
    id: str = Field(default="", max_length=64)
    name: str = Field(min_length=1, max_length=64)
    platform: Literal["windows", "android", "web"] = "windows"
    paired: bool = False


class SettingsIn(BaseModel):
    language: Literal["en", "bn", "banglish"] | None = None
    default_model: str | None = Field(default=None, max_length=200)
    theme: Literal["dark", "light"] | None = None
