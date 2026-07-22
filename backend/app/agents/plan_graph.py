"""M5.5 · Plan Graph。

StateGraph(PlanState):
    START → planner → [interrupt() 自动暂停]
            ↓ resume approve → 条件路由 → researcher (create_agent subgraph) → END
            ↓ resume reject  → END

checkpointer 复用 research_agent.get_saver() 单例（同一 SQLite DB，同一连接，
thread_id=session_id 与现有 /api/research/stream 物理分桶一致）。

**关键决定**：
- planner_node 是纯 LLM node，interrupt() 在它里面安全（无副作用，resume 重跑不双执行）
- create_agent() 作为 subgraph 嵌入，tools/state 完全不动
- conditional edge 按 plan_status 路由（approve → researcher，reject/其它 → END）
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents.research_agent import build_agent, get_saver
from app.agents.nodes.planner import planner_node
from app.agents.state import ResearchState


def _route_after_planner(state: dict) -> str:
    """planner 完成后路由：approved → researcher，其它 → END。

    ponytail：plan_status 用字符串比较；不为 "approved" 一律 END（包括 pending 兜底，
    实际 pending 状态由 interrupt() 抛 GraphInterrupt 拦住，不会走到这里）。
    """
    return "researcher" if state.get("plan_status") == "approved" else END


def build_plan_graph():
    """构造 plan graph（编译后）。调用前必须 init_saver（lifespan）。"""
    saver = get_saver()
    builder = StateGraph(ResearchState)

    # planner 节点（含 interrupt）
    builder.add_node("planner", planner_node)

    # researcher 节点 = 现有 create_agent（ReAct agent 完全不动）
    researcher = build_agent()
    builder.add_node("researcher", researcher)

    builder.add_edge(START, "planner")
    builder.add_conditional_edges(
        "planner",
        _route_after_planner,
        {"researcher": "researcher", END: END},
    )
    builder.add_edge("researcher", END)

    return builder.compile(checkpointer=saver)