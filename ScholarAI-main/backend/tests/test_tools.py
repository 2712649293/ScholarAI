"""M5.5.9 · tools.py search_arxiv ToolMessage 翻译测试。

mock searcher.run 抛 ArxivFetchFailed，验证 tool 翻译成 ToolMessage（不冒泡）。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

from langchain_core.messages import ToolMessage
from langchain.tools import ToolRuntime
from langgraph.types import Command

from app.agents import tools as tools_mod
from app.errors import ArxivFetchFailed


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


def test_search_arxiv_tool_translates_fetch_failed_to_toolmessage():
    """M5.5.9: searcher.run 抛 ArxivFetchFailed → tool 返回 Command 含 ToolMessage。"""
    tool_list = tools_mod.make_tools()
    search_arxiv = tool_list[0]

    async def main():
        with patch(
            "app.agents.nodes.searcher.run",
            new=AsyncMock(side_effect=ArxivFetchFailed("arxiv 检索全部失败", details={"reason": "all_timeout"})),
        ):
            result = await search_arxiv.ainvoke(
                {"queries": ["q1", "q2"], "runtime": _runtime()}
            )
        assert isinstance(result, Command)
        assert "messages" in result.update
        msgs = result.update["messages"]
        assert len(msgs) == 1
        assert isinstance(msgs[0], ToolMessage)
        assert msgs[0].tool_call_id == "call-1"
        assert "arxiv 检索失败" in msgs[0].content
        assert "建议" in msgs[0].content

    asyncio.run(main())


def test_search_arxiv_tool_succeeds_normally():
    """正常路径：searcher.run 返结果 → tool 返回 Command 含 papers + search_queries。"""
    from langchain_core.messages import ToolMessage as TM

    tool_list = tools_mod.make_tools()
    search_arxiv = tool_list[0]

    fake_hits = [
        {"arxiv_id": "2401.00001v1", "title": "Paper 1", "authors": ["A"], "year": 2024, "abstract": "abs1", "pdf_url": "url1"},
        {"arxiv_id": "2401.00002v1", "title": "Paper 2", "authors": ["B"], "year": 2023, "abstract": "abs2", "pdf_url": "url2"},
    ]

    async def main():
        with patch(
            "app.agents.nodes.searcher.run",
            new=AsyncMock(return_value={"papers": fake_hits}),
        ):
            result = await search_arxiv.ainvoke(
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