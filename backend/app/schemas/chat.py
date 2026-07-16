"""问答模式请求/响应 schema。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Citation(BaseModel):
    kb_id: str
    doc_id: str
    page: int
    chunk_index: int
    text: str
    score: float


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    session_id: str | None = Field(None, description="会话 id；为空则创建新会话")
    max_history: int = Field(10, ge=0, le=20, description="携带的历史消息轮数")
    kb_ids: list[str] = Field(default_factory=list, max_length=10, description="挂载的知识库")


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    echo: bool = False
    citations: list[Citation] = Field(default_factory=list)
