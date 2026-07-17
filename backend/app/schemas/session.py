"""会话（session）Pydantic schemas（M2.6.3）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SessionSummary(BaseModel):
    id: str
    title: str
    mode: str
    status: str
    message_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    extra: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionDetail(SessionSummary):
    messages: list[MessageOut]


class SessionUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
