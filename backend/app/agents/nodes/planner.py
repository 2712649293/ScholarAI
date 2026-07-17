"""Planner 节点：把研究方向拆成子问题 + arxiv 检索词（M3.2）。"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field

from app.agents.state import ResearchState
from app.llm import call_llm


class PlannerOutput(BaseModel):
    sub_questions: list[str] = Field(..., description="3-5 个具体子问题（中文）")
    search_queries: list[str] = Field(..., description="3-5 个 arxiv 英文检索短语（每个 2-5 词）")


_parser = PydanticOutputParser(pydantic_object=PlannerOutput)

SYSTEM = (
    "你是学术研究规划助手。把用户的研究方向拆成 3-5 个具体子问题，"
    "并生成 3-5 个适合 arxiv 检索的英文关键词短语。\n{format_instructions}"
)


async def run(state: ResearchState) -> ResearchState:
    messages = [
        SystemMessage(
            content=SYSTEM.format(format_instructions=_parser.get_format_instructions())
        ),
        HumanMessage(content=f"研究方向：{state['query']}"),
    ]
    raw = await call_llm(messages)
    parsed = _parser.parse(raw)
    return {
        "sub_questions": parsed.sub_questions,
        "search_queries": parsed.search_queries,
    }
