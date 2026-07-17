"""研究模式请求/响应 schema（M3.3）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    max_papers: int = Field(20, ge=1, le=100)
    depth: Literal["quick", "normal", "deep"] = "normal"
    session_id: str | None = None


class PaperOut(BaseModel):
    arxiv_id: str
    title: str
    authors: list[str]
    year: int
    abstract: str
    pdf_url: str | None = None


class ResearchResponse(BaseModel):
    session_id: str
    report_markdown: str
    report_path: str
    papers: list[PaperOut]
