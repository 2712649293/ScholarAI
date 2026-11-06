"""研究模式请求/响应 schema（M3.3）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    max_papers: int = Field(20, ge=1, le=100)
    research_mode: Literal["quick", "deep"] = "quick"
    session_id: str | None = None


class PaperOut(BaseModel):
    paper_key: str | None = None
    arxiv_id: str | None = None
    title: str
    authors: list[str]
    year: int
    abstract: str
    doi: str | None = None
    source_ids: dict[str, str] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    pdf_url: str | None = None
    landing_url: str | None = None
    is_open_access: bool = False
    download_status: str | None = None


class ResearchResponse(BaseModel):
    session_id: str
    report_markdown: str
    report_path: str
    papers: list[PaperOut]
    failure_reason: str | None = None  # search_failed | download_failed | analysis_failed | review_failed | None
