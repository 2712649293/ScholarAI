"""研究模式 LangGraph 装配（M3.3）。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents.nodes import planner, searcher, synthesizer
from app.agents.state import ResearchState


def build_graph():
    """线性图：planner → searcher → synthesizer。M4 再加 downloader/analyzer/reviewer。"""
    g = StateGraph(ResearchState)
    g.add_node("planner", planner.run)
    g.add_node("searcher", searcher.run)
    g.add_node("synthesizer", synthesizer.run)
    g.add_edge(START, "planner")
    g.add_edge("planner", "searcher")
    g.add_edge("searcher", "synthesizer")
    g.add_edge("synthesizer", END)
    return g.compile()


# ponytail: 模块级单例；MemorySaver checkpointer 留 M4/§12.5 再加
graph = build_graph()
