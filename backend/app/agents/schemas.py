"""M5.5 · Plan 模块数据 schema。

- ResearchPlan: LLM 生成的结构化计划（planner 节点输出 + plan 字段值）
- API 请求/响应 schema：PlanRequest / PlanEditRequest / ApproveRequest / PlanResponse
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# === 内部：LLM 结构化输出 ===
class SubQuestion(BaseModel):
    """单个子问题 + 一句 rationale（为什么这个问题对回答主方向重要）。"""

    question: str
    rationale: str


class SearchQueryGroup(BaseModel):
    """一组检索词（按子问题分组），每个 2-5 个英文短语。"""

    intent: str
    queries: list[str] = Field(..., min_length=2, max_length=5)


class OutlineSection(BaseModel):
    """综述章节：标题 + 1-4 个具体 bullet。"""

    heading: str
    bullets: list[str] = Field(..., min_length=1, max_length=4)


class ResearchPlan(BaseModel):
    """完整研究计划。LLM 输出约束（with_structured_output / JSON mode fallback）。"""

    title: str
    sub_questions: list[SubQuestion] = Field(..., min_length=3, max_length=5)
    search_queries: list[SearchQueryGroup] = Field(..., min_length=3, max_length=5)
    outline: list[OutlineSection] = Field(..., min_length=5, max_length=7)
    estimated_papers: int = Field(..., ge=5, le=50)
    reasoning: str


# === API 请求 ===
class PlanRequest(BaseModel):
    """POST /api/research/plan 启动 plan 生成。"""

    query: str
    session_id: str | None = None
    depth: Literal["quick", "normal", "deep"] = "normal"
    max_papers: int = 20


class PlanEditRequest(BaseModel):
    """PATCH /api/research/{sid}/plan 用户编辑 plan 字段（不批准执行）。"""

    plan: dict  # 至少含 sub_questions / search_queries / outline 任一字段


class ApproveRequest(BaseModel):
    """POST /api/research/{sid}/plan/approve 批准 plan → 续接执行。"""

    edited_plan: dict | None = None  # 可选：批准时传最终 plan


class ContinueRequest(BaseModel):
    """POST /api/research/{sid}/continue 追问请求（M5.5.6）。

    ponytail：只传 query，agent 通过 checkpointer 自动恢复 state（含 plan / draft / papers）。
    """

    query: str


# === API 响应 ===
class PlanResponse(BaseModel):
    """plan 状态读取响应（GET / PATCH / reject / regenerate 共用）。"""

    session_id: str
    plan: dict | None = None
    plan_status: str  # "none" | "pending" | "edited" | "approved" | "rejected"
    plan_generated_at: str | None = None