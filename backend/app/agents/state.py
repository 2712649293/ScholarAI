"""研究模式 LangGraph 状态定义（M4.5.1：完整 langgraph state schema）。

全部状态经 checkpointer 持久化（thread_id=session_id）。
- messages 用 add_messages reducer（自动追加，不覆盖）
- 检索词/论文支持并行工具合并，其他字段直接覆盖
"""
from __future__ import annotations

from langchain.agents import AgentState
from typing import Annotated
from typing_extensions import TypedDict


def merge_queries(left: list[str], right: list[str]) -> list[str]:
    return list(dict.fromkeys(left + right))


def merge_papers(left: list[dict], right: list[dict]) -> list[dict]:
    by_id = {paper["arxiv_id"]: dict(paper) for paper in left}
    for paper in right:
        current = by_id.setdefault(paper["arxiv_id"], {})
        local_path = current.get("local_path") or paper.get("local_path")
        current.update(paper)
        if local_path:
            current["local_path"] = local_path
    return list(by_id.values())


class ResearchContext(TypedDict, total=False):
    """研究流程共享状态。checkpointer 按 thread_id 持久化整个对象。"""

    # === 请求上下文（每次 invoke 注入，工具读） ===
    query: str
    depth: str  # quick | normal | deep
    max_papers: int
    session_id: str

    # === workflow 产物（工具通过 Command 写） ===
    sub_questions: list[str]
    search_queries: Annotated[list[str], merge_queries]
    papers: Annotated[list[dict], merge_papers]  # arxiv 检索结果（含 local_path）
    download_failures: list[str]  # 下载失败的 arxiv_id
    analyses: list[dict]  # 逐篇结构化分析
    draft: str  # 综述草稿（refine 模式用此字段做 diff）
    feedback: str  # 审校意见
    iteration: int

    # 恢复追问时保持本轮的回复选择与历史写入上下文。
    run_query: str
    run_draft_before: str | None
    run_msg_count_before: int

    # === Plan 模块（M5.5：用户可审可改的预执行计划）===
    plan: dict | None  # ResearchPlan.model_dump()
    plan_status: str  # "pending" | "edited" | "approved" | "rejected" | "clarifying"（M5.6）
    plan_generated_at: str | None  # ISO timestamp（plan 首次生成时间）

    # === M5.6：澄清阶段 ===
    clarify_direction: dict | None  # confirm_direction 工具保存的确认信息
    # {refined_query, year_start, year_end, notes, confirmed_at}


class ResearchState(AgentState, ResearchContext):
    """图的完整状态；消息与reducer来自AgentState，业务字段为可选增量。"""
