"""研究模式 LangGraph 状态定义（M4.5.1：完整 langgraph state schema）。

全部状态经 checkpointer 持久化（thread_id=session_id）。
- messages 用 add_messages reducer（自动追加，不覆盖）
- 其他字段直接覆盖
"""
from __future__ import annotations

from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class ResearchState(TypedDict, total=False):
    """研究流程共享状态。checkpointer 按 thread_id 持久化整个对象。"""

    # === langgraph 必备：消息历史（reducer 追加） ===
    messages: Annotated[list[BaseMessage], add_messages]

    # === 请求上下文（每次 invoke 注入，工具读） ===
    query: str
    depth: str  # quick | normal | deep
    max_papers: int
    session_id: str

    # === workflow 产物（工具通过 Command 写） ===
    sub_questions: list[str]
    search_queries: list[str]
    papers: list[dict]  # arxiv 检索结果（含 local_path）
    download_failures: list[str]  # 下载失败的 arxiv_id
    analyses: list[dict]  # 逐篇结构化分析
    draft: str  # 综述草稿（refine 模式用此字段做 diff）
    feedback: str  # 审校意见
    iteration: int
