"""真实图/SQLite检查点与ASGI断开；仅替换外部模型和检索。"""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage

from app import research_runs
from app.agents import research_agent
from app.api import research
from app.db.models import SessionModel
from app.db.session import SessionLocal
from app.session_store import store
from tests.test_research_plan_api import _patch_planner_llm
from tests.test_review_regressions import ScriptedModel


def search_script(count=5):
    return ScriptedModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "search_arxiv", "args": {"queries": [f"q{i}"]},
                                          "id": f"call-{i}"} for i in range(count)]),
        AIMessage(content="搜索完成"),
    ])


def final_data(body):
    return next(json.loads(block.split("data: ", 1)[1]) for block in body.split("\n\n")
                if block.startswith("event: final"))


async def consume(response):
    return "".join([chunk async for chunk in response.body_iterator])


def test_disconnect_keeps_nested_parallel_search_running_and_replays_once(client):
    sid = "recovery-disconnect"
    with _patch_planner_llm():
        client.post("/api/research/plan", json={"query": "方向", "session_id": sid, "max_papers": 3})

    async def scenario():
        release = asyncio.Event()
        started = asyncio.Event()
        finished = []

        async def search(state):
            started.set()
            await release.wait()
            query = state["search_queries"][0]
            finished.append(query)
            return {"papers": [{"arxiv_id": "common", "title": "共同论文"},
                               {"arxiv_id": query, "title": query}]}

        with patch("app.agents.research_agent.get_llm", return_value=search_script()), \
             patch("app.agents.nodes.searcher.run", side_effect=search):
            response = await research.approve_plan(sid)
            run = research_runs.runs[sid]
            disconnected = asyncio.Event()
            received = []

            async def receive():
                await disconnected.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                body = message.get("body", b"").decode()
                received.append(body)
                if "event: step" in body:
                    disconnected.set()

            # Starlette's real disconnect cancellation path, before any tool returns.
            await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send), 5)
            await asyncio.wait_for(started.wait(), 5)
            assert any("search_arxiv" in item for item in received)
            assert not finished and not run.finished and not run.task.cancelled()
            status = await research.get_plan(sid)
            assert status.execution_status == "running" and not status.can_resume
            for action in (lambda: research.approve_plan(sid), lambda: research.resume_research(sid),
                           lambda: research.continue_research_stream(sid, research.ContinueRequest(query="追问"))):
                with pytest.raises(HTTPException) as exc:
                    await action()
                assert exc.value.status_code == 409
            release.set()
            body = await asyncio.wait_for(consume(await research.research_events(sid)), 5)
            assert body.count("event: step") == 5
            assert body.count("event: final") == 1
            final = final_data(body)
            assert len(final["papers"]) == 3
            assert len({paper["arxiv_id"] for paper in final["papers"]}) == 3
            replay = await consume(await research.research_events(sid))
            assert final_data(replay)["message_id"] == final["message_id"]
            assert (await research.get_plan(sid)).execution_status == "done"
            assert len(finished) == 5

    client.portal.call(scenario)
    history = client.get(f"/api/sessions/{sid}").json()["messages"]
    assert [message["content"] for message in history] == ["方向", research.NO_REPORT]
    assert len(list(Path(research.settings.reports_dir).glob(f"{sid}_*.md"))) == 1


def test_resume_nested_pending_tools_without_replanning(client):
    sid = "recovery-resume"
    with _patch_planner_llm():
        client.post("/api/research/plan", json={"query": "方向", "session_id": sid, "max_papers": 4})

    async def scenario():
        started = asyncio.Event()

        async def blocked(_state):
            started.set()
            await asyncio.Event().wait()

        model = search_script()
        # Legacy child graph has LastValue papers/queries and no new cap middleware.
        from typing import get_type_hints
        from typing_extensions import TypedDict
        from langchain.agents import create_agent
        from app.agents.state import ResearchState
        from app.agents.tools import make_tools
        fields = get_type_hints(ResearchState, include_extras=True)
        fields.update(papers=list[dict], search_queries=list[str])
        legacy_state = TypedDict("LegacyState", fields, total=False)

        def legacy_agent():
            return create_agent(model, make_tools(), state_schema=legacy_state,
                                checkpointer=research_agent.get_saver())

        with patch("app.agents.research_agent.get_llm", return_value=model), \
             patch("app.agents.nodes.searcher.run", side_effect=blocked), \
             patch("app.agents.plan_graph.build_agent", side_effect=legacy_agent):
            await research.approve_plan(sid)
            await asyncio.wait_for(started.wait(), 5)
            await research_runs.cancel(sid)
        # Simulate restarting the service: registry is gone, SQLite child checkpoint survives.
        with patch("app.agents.research_agent.get_llm", return_value=model):
            status = await research.get_plan(sid)
            assert status.plan_status == "approved" and status.can_resume
            assert status.execution_status == "failed"
            config = research._agent_config(sid)
            graph = await research._build_plan_graph()
            snap = await graph.aget_state(config, subgraphs=True)
            assert snap.next == ("researcher",)
            assert snap.tasks[0].state.next == ("tools",) * 5
            before = snap.values["plan_generated_at"]

            async def search(state):
                query = state["search_queries"][0]
                return {"papers": [{"arxiv_id": query, "title": query}]}

            with patch("app.agents.nodes.searcher.run", side_effect=search), \
                 patch("app.agents.nodes.planner.generate_plan", side_effect=AssertionError("不能重新规划")):
                body = await asyncio.wait_for(consume(await research.resume_research(sid)), 5)
            assert "event: final" in body and "event: error" not in body
            assert body.count("event: step") == 5
            assert len(final_data(body)["papers"]) == 4
            snap = await graph.aget_state(config)
            assert not snap.next and snap.values["plan_generated_at"] == before
            assert len([m for m in snap.values["messages"] if m.type == "tool"]) == 5
            assert not (await research.get_plan(sid)).can_resume
            with pytest.raises(HTTPException) as exc:
                await research.resume_research(sid)
            assert exc.value.status_code == 409

    client.portal.call(scenario)
    assert len(client.get(f"/api/sessions/{sid}").json()["messages"]) == 2


def test_resume_followup_preserves_query_and_answer(client):
    sid = "recovery-followup"
    store.get_or_create(sid, mode="research")

    async def scenario():
        started = asyncio.Event()

        async def blocked(_state):
            started.set()
            await asyncio.Event().wait()

        model = search_script(1)
        with patch("app.agents.research_agent.get_llm", return_value=ScriptedModel(responses=[AIMessage(content="旧回复")])):
            agent = research_agent.build_agent()
            await agent.ainvoke({"session_id": sid, "query": "原方向", "plan_status": "approved",
                                 "draft": "旧报告", "messages": [HumanMessage(content="原方向")]},
                                config=research._agent_config(sid))
        with patch("app.agents.research_agent.get_llm", return_value=model):
            with patch("app.agents.nodes.searcher.run", side_effect=blocked):
                await research.continue_research_stream(sid, research.ContinueRequest(query="新追问"))
                await asyncio.wait_for(started.wait(), 5)
                await research_runs.cancel(sid)
            with patch("app.agents.nodes.searcher.run", return_value={"papers": [{"arxiv_id": "x", "title": "x"}]}):
                body = await asyncio.wait_for(consume(await research.resume_research(sid)), 5)
            assert final_data(body)["report_markdown"] == "搜索完成"

    client.portal.call(scenario)
    history = client.get(f"/api/sessions/{sid}").json()["messages"]
    assert [message["content"] for message in history] == ["新追问", "搜索完成"]


def test_delete_active_run_awaits_cancellation_and_clears_checkpoint(client):
    sid = "recovery-delete"
    store.get_or_create(sid, mode="research")

    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def work(run):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return {}

        run = research_runs.start(sid, work)
        await started.wait()
        from app.api.sessions import delete_session
        with SessionLocal() as db:
            await delete_session(sid, db)
        assert cancelled.is_set() and run.task.done()
        assert sid not in research_runs.runs and store.get(sid) is None

    client.portal.call(scenario)
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_startup_marks_only_running_research_failed():
    for sid, mode in (("recovery-startup", "research"), ("recovery-qa", "qa")):
        store.get_or_create(sid, mode=mode)
        research_runs._status(sid, "running")
    research_runs.recover_interrupted()
    with SessionLocal() as db:
        assert db.get(SessionModel, "recovery-startup").status == "failed"
        assert db.get(SessionModel, "recovery-qa").status == "running"


def test_merge_keeps_downloaded_path_when_parallel_search_is_stale():
    from app.agents.state import merge_papers
    result = merge_papers([{"arxiv_id": "x", "local_path": "/papers/x.pdf"}],
                          [{"arxiv_id": "x", "title": "x", "local_path": ""}])
    assert result == [{"arxiv_id": "x", "local_path": "/papers/x.pdf", "title": "x"}]


def test_recursion_failure_is_visible_and_does_not_finalize_success(client):
    sid = "recovery-recursion"
    with _patch_planner_llm():
        client.post("/api/research/plan", json={"query": "方向", "session_id": sid})
    model = ScriptedModel(responses=[AIMessage(content="不应该到达")])
    real_config = research._agent_config
    with patch("app.agents.research_agent.get_llm", return_value=model), \
         patch.object(research, "_agent_config", side_effect=lambda thread: {**real_config(thread), "recursion_limit": 1}):
        response = client.post(f"/api/research/{sid}/plan/approve")
        assert "event: error" in response.text and "event: final" not in response.text
        status = client.get(f"/api/research/{sid}/plan").json()
    assert status["plan_status"] == "approved"
    assert status["execution_status"] == "failed" and status["can_resume"]
    assert client.get(f"/api/sessions/{sid}").json()["messages"] == []
    assert not list(Path(research.settings.reports_dir).glob(f"{sid}_*.md"))
