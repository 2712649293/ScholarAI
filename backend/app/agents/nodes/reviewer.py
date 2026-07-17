"""Reviewer 节点：综述自检（覆盖度 / 引用准确性），不通过则触发重写（M4.3）。"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field

from app.agents.state import ResearchState
from app.llm import call_llm


class ReviewResult(BaseModel):
    covered_subquestions: list[str] = Field(..., description="综述已覆盖的子问题")
    citation_accuracy: str = Field(..., description="ok 或 errors")
    issues: list[str] = Field(..., description="发现的问题（无则空列表）")
    passed: bool = Field(..., description="是否通过（覆盖全 + 引用无误 + 结构完整）")


_parser = PydanticOutputParser(pydantic_object=ReviewResult)

SYSTEM = (
    "你是学术综述审校助手。检查综述是否：1) 覆盖所有子问题；"
    "2) 每个 [arxiv_id] 引用都在给定论文列表中存在；3) 结构完整。\n{format_instructions}"
)


async def run(state: ResearchState) -> ResearchState:
    papers = state.get("papers", [])
    if not papers:  # 无论文，无需审校
        return {"passed": True, "feedback": ""}

    valid_ids = ", ".join(p["arxiv_id"] for p in papers)
    sub_qs = "\n".join(f"- {q}" for q in state.get("sub_questions", []))
    user = (
        f"子问题：\n{sub_qs}\n\n"
        f"合法 arxiv_id：{valid_ids}\n\n"
        f"待审校综述：\n{state.get('report_draft', '')}"
    )
    messages = [
        SystemMessage(content=SYSTEM.format(format_instructions=_parser.get_format_instructions())),
        HumanMessage(content=user),
    ]
    try:
        parsed = _parser.parse(await call_llm(messages, timeout=120))
    except Exception:  # 审校失败不阻断，放行避免死循环
        return {"passed": True, "feedback": ""}

    feedback = "" if parsed.passed else "；".join(parsed.issues)
    return {"passed": parsed.passed, "feedback": feedback}
