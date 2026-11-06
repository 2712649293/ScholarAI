"""Reviewer 节点：综述自检（覆盖度 / 引用准确性），不通过则触发重写（M4.3）。"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from app.agents.state import ResearchContext
from app.agents.nodes.synthesizer import _format_papers, analysis_for
from app.llm import call_llm
from app.search.models import citation_id


class ReviewResult(BaseModel):
    covered_subquestions: list[str] = Field(..., description="综述已覆盖的子问题")
    citation_accuracy: str = Field(..., description="ok 或 errors")
    issues: list[str] = Field(..., description="发现的问题（无则空列表）")
    passed: bool = Field(..., description="是否通过（覆盖全 + 引用无误 + 结构完整）")


_parser = PydanticOutputParser(pydantic_object=ReviewResult)

SYSTEM = (
    "你是学术综述审校助手。检查综述是否：1) 覆盖所有子问题；"
    "2) 每个 [论文引用标识] 都在给定论文列表中存在；3) 结构完整；4) 摘要证据明确标注，不能冒充全文分析或推断摘要没有的实验细节。\n{format_instructions}"
)


class ReviewUpdate(TypedDict):
    passed: bool
    feedback: str
    status: str


async def run(state: ResearchContext) -> ReviewUpdate:
    papers = state.get("papers", [])
    if not papers:  # 无论文，无需审校
        return {"passed": False, "feedback": "没有论文证据，无法完成审校", "status": "failed"}

    valid_ids = ", ".join(citation_id(p) for p in papers)
    sub_qs = "\n".join(f"- {q}" for q in state.get("sub_questions", []))
    user = (
        f"子问题：\n{sub_qs}\n\n"
        f"合法论文引用标识：{valid_ids}\n\n"
        f"待审校综述：\n{state.get('draft', '')}"
    )
    if state.get("research_mode") == "deep":
        analyses = [a for p in papers if p.get("local_path") and
                    (a := analysis_for(p, state.get("analyses", [])))]
        user += "\n\n论文证据（全文分析或仅摘要）：\n" + _format_papers(papers, analyses)
    messages = [
        SystemMessage(content=SYSTEM.format(format_instructions=_parser.get_format_instructions())),
        HumanMessage(content=user),
    ]
    try:
        parsed = _parser.parse(await call_llm(messages, timeout=120))
    except Exception:
        return {
            "passed": False,
            "feedback": "审校服务不可用，请人工复核报告",
            "status": "failed",
        }

    feedback = "" if parsed.passed else "；".join(parsed.issues)
    return {
        "passed": parsed.passed,
        "feedback": feedback,
        "status": "passed" if parsed.passed else "failed",
    }
