"""M5.6 · Clarify 阶段 API（planner 前的意图确认）。

- POST /api/research/clarify/stream: 首次启动澄清对话
- POST /api/research/{sid}/clarify/continue: 用户回复后续

SSE 事件：
- message: {content} — agent 文本回复（追加到前端聊天流）
- step: {node} — agent 调了工具
- confirmed: {session_id, clarify_direction, plan_status} — 确认完成，触发前端调 /api/research/plan
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from app.agents.research_agent import RECURSION_LIMIT
from app.agents.schemas import ClarifyRequest, ContinueRequest
from app.observability.callbacks import HANDLER

router = APIRouter(prefix="/api/research", tags=["research"])


def _agent_config(thread_id: str) -> dict:
    return {
        "recursion_limit": RECURSION_LIMIT,
        "callbacks": [HANDLER],
        "configurable": {"thread_id": thread_id},
    }


def _sse(event: str, data: dict) -> str:
    import json

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_round(graph: Any, config: dict, initial: dict, sid: str, query: str) -> AsyncIterator[str]:
    from app.session_store import store

    yield _sse("session", {"session_id": sid})
    snap_before = await graph.aget_state(config)
    count_before = len(snap_before.values.get("messages", [])) if snap_before else 0
    store.add_message(sid, "user", query)
    seen: set[str] = set()
    try:
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
                        yield _sse("step", {"node": tc["name"]})
                    if type(message).__name__ == "AIMessage" and message.content and not message.tool_calls:
                        content = message.text
                        store.add_message(sid, "assistant", content)
                        yield _sse("message", {"content": content})
        snap = await graph.aget_state(config)
        state = snap.values if snap else {}
        if state.get("plan_status") == "pending" and state.get("clarify_direction"):
            yield _sse("confirmed", {
                "session_id": sid,
                "clarify_direction": state["clarify_direction"],
                "plan_status": "pending",
            })
        yield _sse("complete", {"session_id": sid})
        yield ": done\n\n"
    except Exception as exc:
        yield _sse("error", {"code": "internal_error", "message": str(exc)})


@router.post("/clarify/stream")
async def clarify_stream(req: ClarifyRequest) -> StreamingResponse:
    """启动澄清；问题必须进入 agent 消息历史，同时保存可供页面恢复的对话。"""
    from app.agents.clarify_graph import build_clarify_graph
    from app.session_store import store

    session = store.get_or_create(req.session_id, mode="research")
    config = _agent_config(session.id)
    graph = build_clarify_graph()
    initial = {
        "query": req.query, "session_id": session.id, "depth": "normal",
        "messages": [HumanMessage(content=req.query)], "plan_status": "clarifying",
    }
    return StreamingResponse(
        _stream_round(graph, config, initial, session.id, req.query),
        media_type="text/event-stream",
    )


@router.post("/{sid}/clarify/continue")
async def clarify_continue(sid: str, req: ContinueRequest) -> StreamingResponse:
    """澄清阶段用户回复。"""
    from app.agents.clarify_graph import build_clarify_graph
    from app.session_store import store

    if store.get(sid) is None:
        raise HTTPException(404, "会话不存在")
    config = _agent_config(sid)
    graph = build_clarify_graph()
    snap = await graph.aget_state(config)
    if not snap or snap.values.get("plan_status") != "clarifying":
        raise HTTPException(409, "会话不在澄清阶段")
    return StreamingResponse(
        _stream_round(graph, config, {"messages": [HumanMessage(content=req.query)]}, sid, req.query),
        media_type="text/event-stream",
    )
