"""P0 research correctness boundaries: mode, ownership, timeout and quality."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app import research_runs
from app.agents.nodes import reviewer
from app.config import settings
from app.session_store import store


def test_session_mode_is_server_side_boundary(client):
    sid = store.get_or_create(None, mode="research").id
    with patch("app.api.chat.call_llm", new=AsyncMock(return_value="ok")):
        response = client.post("/api/chat/qa", json={"session_id": sid, "query": "q"})
    assert response.status_code == 409
    assert response.json()["code"] == "session_mode_conflict"

    qa_sid = store.get_or_create(None, mode="qa").id
    response = client.post("/api/research/plan", json={"session_id": qa_sid, "query": "q"})
    assert response.status_code == 409
    assert response.json()["code"] == "session_mode_conflict"


@pytest.mark.asyncio
async def test_run_claim_rejects_second_operation():
    sid = store.get_or_create("p0-claim", mode="research").id
    first = research_runs.claim(sid, "clarify")
    with pytest.raises(HTTPException) as exc:
        research_runs.claim(sid, "execute")
    assert exc.value.status_code == 409
    research_runs.finish_claim(first)


@pytest.mark.asyncio
async def test_run_timeout_marks_timed_out(monkeypatch):
    sid = store.get_or_create("p0-timeout", mode="research").id
    monkeypatch.setattr(settings, "research_timeout_seconds", 0.01)

    async def blocked(_run):
        await asyncio.sleep(1)
        return {}

    run = research_runs.start(sid, blocked)
    await asyncio.wait_for(run.task, 1)
    assert run.error_code == "research_timeout"
    assert run.events[0][0] == "error"


@pytest.mark.asyncio
async def test_user_cancel_is_visible_and_keeps_run_for_reconnect():
    sid = store.get_or_create("p0-cancel", mode="research").id
    started = asyncio.Event()

    async def blocked(_run):
        started.set()
        await asyncio.Event().wait()
        return {}

    run = research_runs.start(sid, blocked)
    await started.wait()
    await research_runs.cancel(sid, reason="user", discard=False)
    assert run.error_code == "cancelled"
    assert research_runs.runs[sid] is run
    assert run.events == [("error", {"code": "cancelled", "message": "研究已取消"})]


@pytest.mark.asyncio
async def test_user_cancel_before_task_first_turn_is_visible():
    sid = store.get_or_create("p0-cancel-early", mode="research").id

    async def blocked(_run):
        await asyncio.Event().wait()
        return {}

    run = research_runs.start(sid, blocked)
    await research_runs.cancel(sid, reason="user", discard=False)
    assert run.error_code == "cancelled"
    assert run.finished is True
    assert run.events == [("error", {"code": "cancelled", "message": "研究已取消"})]


@pytest.mark.asyncio
async def test_reviewer_failure_is_not_reported_as_passed():
    with patch.object(reviewer, "call_llm", new=AsyncMock(side_effect=RuntimeError("down"))):
        result = await reviewer.run({
            "papers": [{"arxiv_id": "p1"}],
            "sub_questions": ["q"],
            "draft": "draft",
        })
    assert result["passed"] is False
    assert result["status"] == "failed"
    assert "人工复核" in result["feedback"]


@pytest.mark.asyncio
async def test_deep_write_review_cannot_bypass_selection_and_analysis():
    from app.agents.tools import make_tools

    runtime = ToolRuntime(
        state={
            "research_mode": "deep",
            "papers": [{"arxiv_id": "p1", "title": "Paper", "abstract": "a"}],
        },
        context=None,
        stream_writer=None,
        config=None,
        tool_call_id="deep-gate",
        store=None,
    )
    result = await make_tools()[3].ainvoke({"runtime": runtime})
    assert isinstance(result, Command)
    message = result.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert "select_papers" in message.content
