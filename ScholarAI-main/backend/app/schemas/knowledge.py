"""知识库 / 文档 Pydantic schemas。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class KBCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class KBResponse(BaseModel):
    id: str
    name: str
    embedding_model: str
    doc_count: int
    vector_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class DocResponse(BaseModel):
    id: str
    kb_id: str
    filename: str
    page_count: int
    status: Literal["pending", "indexed", "failed"]
    error: str | None = None
    created_at: datetime
    indexed_at: datetime | None = None

    model_config = {"from_attributes": True}


class SearchHit(BaseModel):
    text: str
    doc_id: str
    page: int
    chunk_index: int
    score: float


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    k: int = Field(5, ge=1, le=20)
