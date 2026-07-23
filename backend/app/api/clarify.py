"""M5.6 · Clarify 阶段 API（planner 前的意图确认）。

- POST /api/research/clarify/stream: 首次启动澄清对话
- POST /api/research/{sid}/clarify/continue: 用户回复后续

SSE 事件：
- message: {content} — agent 文本回复（追加到前端聊天流）
- step: {node} — agent 调了工具
- confirmed: {session_id, clarify_direction, plan_status} — 确认完成，触发前端调 /api/research/plan
"""
from __future__ import annotations

from fastapi import APIRouter
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


@router.post("/clarify/stream")
async def clarify_stream(req: ClarifyRequest) -> StreamingResponse:
    """启动澄清对话。跑 clarify_agent，SSE 推 message/confirmed。"""
    from app.agents.clarify_graph import build_clarify_graph
    from app.session_store import store

    session = store.get_or_create(req.session_id, mode="research")
    config = _agent_config(session.id)
    graph = build_clarify_graph()
    initial = {"query": req.query, "session_id": session.id, "depth": "normal"}

    async def gen():
        try:
            async for chunk in graph.astream(initial, config=config, stream_mode="updates"):
                if "__interrupt__" in (chunk or {}):
                    continue
                for update in (chunk or {}).values():
                    for m in (update or {}).get("messages", []):
                        for tc in getattr(m, "tool_calls", None) or []:
                            yield _sse("step", {"node": tc["name"]})
                        if type(m).__name__ == "AIMessage" and m.content and not m.tool_calls:
                            yield _sse("message", {"content": m.content})
        except Exception as e:
            yield _sse("error", {"code": "internal_error", "message": str(e)})
            return

        snap = await graph.aget_state(config)
        s = snap.values if snap else {}
        if s.get("plan_status") == "pending" and s.get("clarify_direction"):
            yield _sse(
                "confirmed",
                {
                    "session_id": session.id,
                    "clarify_direction": s["clarify_direction"],
                    "plan_status": "pending",
                },
            )
        yield ": done\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/{sid}/clarify/continue")
async def clarify_continue(sid: str, req: ContinueRequest) -> StreamingResponse:
    """澄清阶段用户回复。追加 HumanMessage，继续跑 clarify agent。"""
    from app.agents.clarify_graph import build_clarify_graph

    config = _agent_config(sid)
    graph = build_clarify_graph()
    new_human = HumanMessage(content=req.query)

    async def gen():
        try:
            async for chunk in graph.astream(
                {"messages": [new_human]},
                stream_mode="updates",
                config=config,
            ):
                if "__interrupt__" in (chunk or {}):
                    continue
                for update in (chunk or {}).values():
                    for m in (update or {}).get("messages", []):
                        for tc in getattr(m, "tool_calls", None) or []:
                            yield _sse("step", {"node": tc["name"]})
                        if type(m).__name__ == "AIMessage" and m.content and not m.tool_calls:
                            yield _sse("message", {"content": m.content})
        except Exception as e:
            yield _sse("error", {"code": "internal_error", "message": str(e)})
            return

        snap = await graph.aget_state(config)
        s = snap.values if snap else {}
        if s.get("plan_status") == "pending" and s.get("clarify_direction"):
            yield _sse(
                "confirmed",
                {
                    "session_id": sid,
                    "clarify_direction": s["clarify_direction"],
                    "plan_status": "pending",
                },
            )
        yield ": done\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")