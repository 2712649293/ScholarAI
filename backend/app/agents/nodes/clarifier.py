"""M5.6 · 澄清阶段 Agent（planner 前的意图确认）。

tools: web_search（理解前沿术语）, confirm_direction（确认研究方向）
agent 与用户对话确认方向/年限/范围，确认后调 confirm_direction 写 state，
触发前端调 /api/research/plan 生成计划。

ponytail: web_search 用 requests + DDG instant answer API。
dev 网络不可达时优雅降级，返回 "搜索不可用"。
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from langchain.tools import tool, ToolRuntime
from langchain_core.messages import ToolMessage
from langgraph.types import Command

CLARIFY_SYSTEM = """你是学术研究意图澄清助手。用户给出了一个研究方向，你的任务是与用户对话，
确保对研究方向的理解达到 90%+ 之后再确认。

**关键规则**：
- 先分析用户 query，若方向不清晰（如"研究无人机"——没说具体子领域），**必须追问**
- 若方向清晰，提供 1-2 个合理细化建议让用户确认
- 检索年限默认**近5年**（{current_year-5}-{current_year}），问用户是否需要调整
- 如果用户用了你不熟悉的术语，用 web_search 工具先了解
- **最多 3 轮对话**。3 轮后即使用户没明确回答也调用 confirm_direction 确认
- 调用 confirm_direction 后**不调其他工具**，输出一句简短确认即结束

**对话风格**：简洁专业，每次只问 1-2 个关键问题，不要让用户一次回答太多。
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _search_web(query: str, timeout: float = 10.0) -> str:
    """用 DuckDuckGo Instant Answer API 搜网页。免费无 key。
    失败返回 "搜索不可用"（graceful degradation）。
    """
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            )
            r.raise_for_status()
            data = r.json()
            parts = []
            abstract = (data.get("Abstract") or "").strip()
            if abstract:
                parts.append(abstract)
            for topic in data.get("RelatedTopics", [])[:3]:
                text = (topic.get("Text") or "").strip()
                if text:
                    parts.append(f"- {text}")
            if parts:
                return "\n".join(parts)
            return f"(搜索 '{query}' 无结果)"
    except Exception:
        return f"(搜索 '{query}' 不可用——网络限制，基于已有知识继续)"


def make_clarify_tools() -> list:
    """构造 2 个 clarify agent 专属工具。不复用 research agent 工具。"""

    def _msg(runtime: ToolRuntime, content: str) -> ToolMessage:
        return ToolMessage(content=content, tool_call_id=runtime.tool_call_id)

    @tool
    async def web_search(query: str, runtime: ToolRuntime) -> ToolMessage:
        """搜索网页了解前沿术语/现状。返回摘要。失败返回"搜索不可用"。"""
        result = _search_web(query)
        return _msg(runtime, result)

    @tool
    async def confirm_direction(
        refined_query: str,
        year_start: int = 2021,
        year_end: int | None = None,
        notes: str = "",
        runtime: ToolRuntime,
    ) -> Command:
        """确认研究方向。调用后 plan 将被生成。

        Args:
            refined_query: 细化后的研究方向（完整的句子，替代用户原始 query）
            year_start: 检索起始年份（默认 2021 即近5年）
            year_end: 检索截止年份（默认当前年份）
            notes: 补充说明（如"侧重MAC层"、"排除物理层"等）
        """
        if year_end is None:
            year_end = datetime.now(timezone.utc).year
        return Command(
            update={
                "clarify_direction": {
                    "refined_query": refined_query,
                    "year_start": year_start,
                    "year_end": year_end,
                    "notes": notes,
                    "confirmed_at": _now_iso(),
                },
                "plan_status": "pending",
                "query": refined_query,
            }
        )

    return [web_search, confirm_direction]