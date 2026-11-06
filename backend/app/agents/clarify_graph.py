"""M5.6 · Clarify Graph（plan 前的意图澄清阶段）。

StateGraph: START → intent_router → clarifier/conversation → END

与 plan_graph 共享 checkpointer（同一 thread_id = session_id）。
clarifier_node 的 agent 只有 2 个工具（web_search + confirm_direction），
与 researcher agent（5 个工具）完全分离。

confirm_direction 工具写入 state.clarify_direction + plan_status=pending 后，
agent 结束。前端收到 SSE message 事件后调用 /api/research/plan 生成 plan。
"""
from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langgraph.graph import END, START, StateGraph

from app.agents.nodes.clarifier import CLARIFY_SYSTEM, make_clarify_tools
from app.agents.intent import classify_request
from app.agents.research_agent import get_saver
from app.agents.research_conversation import build_agent as build_conversation_agent
from app.agents.state import ResearchState
from app.llm import get_llm


def _route_intent(state: ResearchState) -> dict:
    result = classify_request(
        state.get("query", ""),
        has_research_context=bool(state.get("plan") or state.get("draft") or state.get("papers")),
    )
    return {"intent": result.intent}


def _intent_edge(state: ResearchState) -> str:
    return "conversation" if state.get("intent") in {"chat", "followup"} else "clarifier"


def build_clarify_graph(*, route_intent: bool = True) -> Any:
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
    if route_intent:
        builder.add_node("intent_router", _route_intent)
        builder.add_node("conversation", build_conversation_agent())
        builder.add_edge(START, "intent_router")
        builder.add_conditional_edges(
            "intent_router", _intent_edge,
            {"clarifier": "clarifier", "conversation": "conversation"},
        )
        builder.add_edge("conversation", END)
    else:
        builder.add_edge(START, "clarifier")

    builder.add_edge("clarifier", END)
    return builder.compile(checkpointer=saver)
