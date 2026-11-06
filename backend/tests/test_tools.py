"""研究检索工具的 MCP 边界测试。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

from langchain_core.messages import ToolMessage
from langchain.tools import ToolRuntime
from langgraph.types import Command

from app.agents import tools as tools_mod
from app.search.models import SearchResult


def _runtime(tool_call_id: str = "call-1") -> ToolRuntime:
    """构造真实 ToolRuntime 实例（dataclass，不是 MagicMock）。"""
    return ToolRuntime(
        state={},
        context=None,
        stream_writer=None,
        config=None,
        tool_call_id=tool_call_id,
        store=None,
    )


def test_search_papers_failure_translates_to_toolmessage():
    tool_list = tools_mod.make_tools()
    search_papers = tool_list[0]

    class FailedSearchAgent:
        async def run(self, state):
            return SearchResult([], state["search_queries"], ["paper-search-mcp"], {"paper-search-mcp": "offline"}, 1, "failed")

    async def main():
        with patch("app.agents.tools.SearchAgent", FailedSearchAgent):
            result = await search_papers.ainvoke(
                {"queries": ["q1", "q2"], "runtime": _runtime()}
            )
        assert isinstance(result, Command)
        assert "messages" in result.update
        msgs = result.update["messages"]
        assert len(msgs) == 1
        assert isinstance(msgs[0], ToolMessage)
        assert msgs[0].tool_call_id == "call-1"
        assert "论文检索失败" in msgs[0].content
        assert "建议" in msgs[0].content

    asyncio.run(main())


def test_search_papers_succeeds_normally():
    from langchain_core.messages import ToolMessage as TM

    tool_list = tools_mod.make_tools()
    search_papers = tool_list[0]

    fake_hits = [
        {"arxiv_id": "2401.00001v1", "title": "Paper 1", "authors": ["A"], "year": 2024, "abstract": "abs1", "pdf_url": "url1"},
        {"arxiv_id": "2401.00002v1", "title": "Paper 2", "authors": ["B"], "year": 2023, "abstract": "abs2", "pdf_url": "url2"},
    ]

    async def main():
        class SuccessfulSearchAgent:
            async def run(self, state):
                return SearchResult(fake_hits, state["search_queries"], ["arxiv"], {}, 1, "completed")

        with patch("app.agents.tools.SearchAgent", SuccessfulSearchAgent):
            result = await search_papers.ainvoke(
                {"queries": ["q1"], "runtime": _runtime()}
            )
        assert isinstance(result, Command)
        assert len(result.update["papers"]) == 2
        assert "q1" in result.update["search_queries"]
        msgs = result.update["messages"]
        assert len(msgs) == 1
        assert isinstance(msgs[0], TM)
        assert "检索完成" in msgs[0].content

    asyncio.run(main())


def test_parallel_search_calls_are_serialized_per_session():
    tool = tools_mod.make_tools()[0]
    calls = {"n": 0}

    class FakeSearchAgent:
        async def run(self, state, profile=None):
            from app.search.models import SearchResult

            calls["n"] += 1
            await asyncio.sleep(0.02)
            return SearchResult(
                papers=[],
                queries=state["search_queries"],
                sources=["arxiv"],
                source_errors={},
                iterations=1,
                status="empty",
            )

    def runtime(call_id: str) -> ToolRuntime:
        return ToolRuntime(
            state={"session_id": "parallel-search", "domain": "general"},
            context=None,
            stream_writer=None,
            config=None,
            tool_call_id=call_id,
            store=None,
        )

    async def main():
        with patch("app.agents.tools.SearchAgent", FakeSearchAgent):
            first, second = await asyncio.gather(
                tool.ainvoke({"queries": ["q1"], "runtime": runtime("c1")}),
                tool.ainvoke({"queries": ["q2"], "runtime": runtime("c2")}),
            )
        assert calls["n"] == 1
        messages = [first.update["messages"][0].content, second.update["messages"][0].content]
        assert any("已有检索正在执行" in message for message in messages)

    asyncio.run(main())
