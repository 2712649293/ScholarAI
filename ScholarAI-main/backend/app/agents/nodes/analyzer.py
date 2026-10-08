"""Analyzer 节点：逐篇 PDF 结构化抽取（M4.2）。"""
from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field

from app.agents.state import ResearchState
from app.llm import call_llm
from app.rag.pdf_loader import load_pdf

MAX_CONCURRENT = 5
MAX_CHARS = 20000  # ~6-8k token，防止超 LLM 上下文（§12 长文先截断）


class PaperAnalysis(BaseModel):
    problem: str = Field(..., description="研究什么问题")
    method: str = Field(..., description="核心方法")
    results: str = Field(..., description="实验结果")
    limitations: str = Field(..., description="局限性")
    novelty: str = Field(..., description="相对已有工作的新意")


_parser = PydanticOutputParser(pydantic_object=PaperAnalysis)

SYSTEM = (
    "你是论文分析助手。阅读给定论文文本，提取结构化信息。只依据文本，不要编造。\n"
    "{format_instructions}"
)


async def _analyze_one(sem: asyncio.Semaphore, paper: dict) -> dict | None:
    path = paper.get("local_path")
    if not path:
        return None  # 未下载成功的跳过
    try:
        pages = await asyncio.to_thread(load_pdf, path)
    except Exception:
        return None  # 解析失败跳过（§8.3 降级）
    text = "\n".join(p.text for p in pages)[:MAX_CHARS]
    if not text.strip():
        return None  # 扫描件/空文本

    messages = [
        SystemMessage(content=SYSTEM.format(format_instructions=_parser.get_format_instructions())),
        HumanMessage(content=f"论文《{paper['title']}》全文（截断）：\n{text}"),
    ]
    async with sem:
        try:
            raw = await call_llm(messages, timeout=120)
            parsed = _parser.parse(raw)
        except Exception:
            return None
    return {"arxiv_id": paper["arxiv_id"], "title": paper["title"], **parsed.model_dump()}


async def run(state: ResearchState) -> ResearchState:
    papers = state.get("papers", [])
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    results = await asyncio.gather(*(_analyze_one(sem, p) for p in papers))
    return {"analyses": [r for r in results if r]}
