"""Synthesizer 节点：基于 abstract 写结构化综述（M3.2）。"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.state import ResearchState
from app.llm import call_llm

SYSTEM = (
    "你是学术综述撰写助手。基于给定论文的标题与摘要，用中文写一篇结构化综述，"
    "包含以下章节：引言、研究现状、方法分类、趋势、未来方向。"
    "每条关键结论用 [arxiv_id] 标注来源（对应下方论文列表）。"
    "只依据给定摘要，不要编造未提及的内容。"
)


def _format_papers(papers: list[dict]) -> str:
    return "\n\n".join(
        f"[{p['arxiv_id']}] ({p['year']}) {p['title']}\n摘要：{p['abstract']}" for p in papers
    )


async def run(state: ResearchState) -> ResearchState:
    papers = state.get("papers", [])
    if not papers:
        return {"report_draft": "未检索到相关论文，无法生成综述。"}

    sub_qs = state.get("sub_questions", [])
    user = (
        f"研究方向：{state['query']}\n\n"
        + "子问题：\n"
        + "\n".join(f"- {q}" for q in sub_qs)
        + f"\n\n论文列表（共 {len(papers)} 篇）：\n{_format_papers(papers)}"
    )
    messages = [SystemMessage(content=SYSTEM), HumanMessage(content=user)]
    draft = await call_llm(messages, timeout=180)  # 综述较长，放宽超时
    return {"report_draft": draft}
