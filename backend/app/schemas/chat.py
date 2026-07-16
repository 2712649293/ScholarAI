"""问答模式请求/响应 schema。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    session_id: str | None = Field(None, description="会话 id；为空则创建新会话")
    max_history: int = Field(10, ge=0, le=20, description="携带的历史消息轮数")


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    echo: bool = False
