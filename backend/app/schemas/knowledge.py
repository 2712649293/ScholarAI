"""知识库 / 文档 Pydantic schemas。"""
from __future__ import annotations

from datetime import datetime
import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
    processing_stage: str = "pending"
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    indexed_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_validator("warnings", mode="before")
    @classmethod
    def decode_warnings(cls, value: str | list[str]) -> list[str]:
        return list(json.loads(value)) if isinstance(value, str) else value


class SearchHit(BaseModel):
    text: str
    doc_id: str
    page: int
    chunk_index: int
    score: float
    chunk_id: str = ""
    page_end: int | None = None
    section: str = ""
    figure_ids: list[str] = Field(default_factory=list)
    source_spans: list[dict] = Field(default_factory=list)
    rerank_score: float | None = None
    retrieval_warnings: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    k: int = Field(5, ge=1, le=20)
