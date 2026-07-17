"""研究模式 LangGraph 状态定义（M3.1）。"""
from __future__ import annotations

from typing import TypedDict


class ResearchState(TypedDict, total=False):
    """研究流程共享状态。M3 所有字段直接覆盖（不用 reducer，M4 再加）。"""

    query: str
    depth: str  # quick | normal | deep
    max_papers: int
    sub_questions: list[str]
    search_queries: list[str]
    papers: list[dict]  # arxiv 检索结果
    analyses: list[dict]  # M4 才填，先空
    report_draft: str
    feedback: str
    iteration: int
