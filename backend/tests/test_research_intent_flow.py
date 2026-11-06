from __future__ import annotations

import asyncio

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage

from app.agents.intent import classify_intent, requested_research_mode
from app.agents.tools import make_tools


def _runtime(state: dict) -> ToolRuntime:
    return ToolRuntime(
        state=state,
        context=None,
        stream_writer=None,
        config=None,
        tool_call_id="flow-test",
        store=None,
    )


def test_research_intent_defaults_to_new_research_for_topic_only_input():
    assert classify_intent("RAG") == "research"
    assert classify_intent("什么是 RAG") == "chat"
    assert classify_intent("请解释刚才的结论", has_research_context=True) == "followup"


def test_research_mode_defaults_quick_and_preserves_explicit_deep():
    assert requested_research_mode("研究 RAG") == "quick"
    assert requested_research_mode("请做深度研究并逐篇分析 RAG") == "deep"


def test_quick_research_hard_blocks_download():
    async def run() -> str:
        result = await make_tools()[1].ainvoke({
            "runtime": _runtime({"research_mode": "quick", "papers": [{"title": "p"}]}),
        })
        message = result.update["messages"][0]
        assert isinstance(message, ToolMessage)
        return message.content

    assert "禁止下载" in asyncio.run(run())


def test_deep_review_cannot_use_abstract_before_download_attempt():
    async def run() -> None:
        runtime = _runtime({
            "research_mode": "deep",
            "query": "q",
            "papers": [{"paper_key": "p", "title": "p", "abstract": "a"}],
            "selected_paper_keys": ["p"],
        })
        result = await make_tools()[3].ainvoke({"runtime": runtime})
        assert "没有可用证据" in result.update["messages"][0].content

    asyncio.run(run())
