"""Clarify phase API with application-owned runs and reconnectable SSE."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from app import research_runs
from app.agents.clarify_graph import build_clarify_graph
from app.agents.intent import classify_request, requested_research_mode
from app.agents.research_agent import RECURSION_LIMIT
from app.agents.schemas import ClarifyRequest, ContinueRequest
from app.observability.callbacks import HANDLER
from app.session_store import store

router = APIRouter(prefix="/api/research", tags=["research"])


def _agent_config(thread_id: str) -> dict:
    return {
        "recursion_limit": RECURSION_LIMIT,
        "callbacks": [HANDLER],
        "configurable": {"thread_id": thread_id},
    }


async def _run_clarify(
    run: research_runs.ResearchRun,
    graph: Any,
    config: dict,
    initial: dict,
    sid: str,
    query: str,
) -> dict:
    """Run one clarify round independently from its HTTP subscriber."""
    yield_session = {"session_id": sid}
    await run.emit("session", yield_session)
    snap_before = await graph.aget_state(config)
    count_before = len(snap_before.values.get("messages", [])) if snap_before else 0
    store.add_message(sid, "user", query)
    seen: set[str] = set()
    async for chunk in graph.astream(initial, config=config, stream_mode="updates"):
        if "__interrupt__" in (chunk or {}):
            continue
        for update in (chunk or {}).values():
            for message in (update or {}).get("messages", [])[count_before:]:
                key = message.id or str(id(message))
                if key in seen:
                    continue
                seen.add(key)
                for tc in getattr(message, "tool_calls", None) or []:
                    await run.emit("step", {"node": tc["name"]})
                if type(message).__name__ == "AIMessage" and message.content and not message.tool_calls:
                    content = message.text
                    store.add_message(sid, "assistant", content)
                    await run.emit("message", {"content": content})
    snap = await graph.aget_state(config)
    state = snap.values if snap else {}
    if state.get("plan_status") == "pending" and state.get("clarify_direction"):
        await run.emit("confirmed", {
            "session_id": sid,
            "clarify_direction": state["clarify_direction"],
            "research_mode": state.get("research_mode", "quick"),
            "plan_status": "pending",
        })
    await run.emit("complete", {"session_id": sid})
    return {"session_id": sid}


async def _start_clarify(
    sid: str,
    query: str,
    initial: dict,
    *,
    existing_run: research_runs.ResearchRun,
) -> StreamingResponse:
    graph = build_clarify_graph(route_intent="intent" in initial)
    config = _agent_config(sid)
    run = research_runs.start(
        sid,
        lambda active: _run_clarify(active, graph, config, initial, sid, query),
        kind="clarify",
        existing=existing_run,
        success_status="idle",
        final_event=None,
    )
    return StreamingResponse(research_runs.stream(sid, run), media_type="text/event-stream")


@router.post("/clarify/stream")
async def clarify_stream(req: ClarifyRequest) -> StreamingResponse:
    """启动澄清；执行独立于 SSE 订阅。"""
    session = store.get_or_create(req.session_id, mode="research")
    run = research_runs.claim(session.id, "clarify", start_status="running")
    try:
        graph = build_clarify_graph()
        config = _agent_config(session.id)
        snap = await graph.aget_state(config)
        if snap and snap.values.get("plan_status") in {"pending", "edited", "approved"}:
            raise HTTPException(409, "会话已有研究计划，请先处理当前计划")
        classification = classify_request(req.query, has_research_context=bool(snap and snap.values.get("draft")))
        initial = {
            "query": req.query,
            "original_query": req.query,
            "clarified_query": req.query,
            "session_id": session.id,
            "intent": classification.intent,
            "research_mode": requested_research_mode(req.query),
            "search_attempts": 0,
            "messages": [HumanMessage(content=req.query)],
            "plan_status": "clarifying" if classification.intent == "research" else "idle",
        }
        return await _start_clarify(session.id, req.query, initial, existing_run=run)
    except Exception:
        research_runs.finish_claim(run)
        raise


@router.post("/{sid}/clarify/continue")
async def clarify_continue(sid: str, req: ContinueRequest) -> StreamingResponse:
    """澄清阶段用户回复；执行独立于 SSE 订阅。"""
    if store.get(sid) is None:
        raise HTTPException(404, "会话不存在")
    run = research_runs.claim(sid, "clarify", start_status="running")
    try:
        graph = build_clarify_graph(route_intent=False)
        config = _agent_config(sid)
        snap = await graph.aget_state(config)
        if not snap or snap.values.get("plan_status") != "clarifying":
            raise HTTPException(409, "会话不在澄清阶段")
        return await _start_clarify(
            sid,
            req.query,
            {"messages": [HumanMessage(content=req.query)]},
            existing_run=run,
        )
    except Exception:
        research_runs.finish_claim(run)
        raise
