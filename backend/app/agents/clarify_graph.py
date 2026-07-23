"""M5.6 · Clarify Graph（plan 前的意图澄清阶段）。

StateGraph: START → clarifier (create_agent) → END

与 plan_graph 共享 checkpointer（同一 thread_id = session_id）。
clarifier_node 的 agent 只有 2 个工具（web_search + confirm_direction），
与 researcher agent（5 个工具）完全分离。

confirm_direction 工具写入 state.clarify_direction + plan_status=pending 后，
agent 结束。前端收到 SSE message 事件后调用 /api/research/plan 生成 plan。
"""
from __future__ import annotations

from langchain.agents import create_agent
from langgraph.graph import END, START, StateGraph

from app.agents.nodes.clarifier import CLARIFY_SYSTEM, make_clarify_tools
from app.agents.research_agent import get_saver
from app.agents.state import ResearchState
from app.llm import get_llm


def build_clarify_graph():
    """构造 clarify graph。checkpointer 共享 _saver 单例。

    ponytail: 单一 node + END。agent 每次 invoke 跑一轮对话。
    用户回复后走 /clarify/continue 再 invoke。
    """
    saver = get_saver()
    builder = StateGraph(ResearchState)
    agent = create_agent(
        get_llm(),
        make_clarify_tools(),
        system_prompt=CLARIFY_SYSTEM.replace(
            "{current_year}", str(__import__("datetime").datetime.now().year)
        ).replace(
            "{current_year-5}", str(__import__("datetime").datetime.now().year - 5)
        ),
        state_schema=ResearchState,
        checkpointer=saver,
    )
    builder.add_node("clarifier", agent)
    builder.add_edge(START, "clarifier")
    builder.add_edge("clarifier", END)
    return builder.compile(checkpointer=saver)