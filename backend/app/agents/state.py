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
    papers: list[dict]  # arxiv 检索结果（M4 起含 local_path）
    download_failures: list[str]  # 下载失败的 arxiv_id（M4.1）
    analyses: list[dict]  # 逐篇结构化分析（M4.2）
    report_draft: str
    feedback: str  # Reviewer 反馈（M4.3）
    passed: bool  # Reviewer 是否通过（M4.3）
    iteration: int
    session_id: str  # M2: per-session paper 目录
    draft: str  # M2.6/§12.11: refine 模式用（原综述）
