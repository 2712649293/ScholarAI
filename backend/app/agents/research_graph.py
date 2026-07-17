"""研究模式 LangGraph 装配（M3.3；M4.3 补全 6 节点 + Reviewer 条件回边）。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents.nodes import analyzer, downloader, planner, reviewer, searcher, synthesizer
from app.agents.state import ResearchState

MAX_ITERATIONS = 2  # Reviewer 最多触发 2 轮重写


def _route_after_review(state: ResearchState) -> str:
    if state.get("passed") or state.get("iteration", 0) >= MAX_ITERATIONS:
        return END
    return "synthesizer"


def build_graph():
    """planner→searcher→downloader→analyzer→synthesizer→reviewer(⟲synthesizer)。"""
    g = StateGraph(ResearchState)
    g.add_node("planner", planner.run)
    g.add_node("searcher", searcher.run)
    g.add_node("downloader", downloader.run)
    g.add_node("analyzer", analyzer.run)
    g.add_node("synthesizer", synthesizer.run)
    g.add_node("reviewer", reviewer.run)

    g.add_edge(START, "planner")
    g.add_edge("planner", "searcher")
    g.add_edge("searcher", "downloader")
    g.add_edge("downloader", "analyzer")
    g.add_edge("analyzer", "synthesizer")
    g.add_edge("synthesizer", "reviewer")
    g.add_conditional_edges(
        "reviewer", _route_after_review, {END: END, "synthesizer": "synthesizer"}
    )
    return g.compile()


# ponytail: 模块级单例；MemorySaver checkpointer 留 §12.5 再加
graph = build_graph()
