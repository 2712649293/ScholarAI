"""研究模式 API（M4.5.1：langgraph checkpointer）。

- 每次 /api/research：构造 initial state dict + thread_id=session_id 调 agent
- checkpointer 自动按 thread_id 持久化整个 state（消息历史 + workflow 产物）
- 跨轮 "agent 接着" = 同一 thread_id 下次 invoke 自动恢复 state
- 多 session 物理隔离 = 不同 thread_id
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langgraph.errors import GraphRecursionError

from app.agents.research_agent import RECURSION_LIMIT, build_agent
from app.config import settings
from app.observability.callbacks import HANDLER
from app.schemas.research import PaperOut, ResearchRequest, ResearchResponse
from app.session_store import store

router = APIRouter(prefix="/api/research", tags=["research"])

# depth 档位 → max_papers 上限（§M3.3）
DEPTH_CAP = {"quick": 8, "normal": 20, "deep": 40}
NO_REPORT = "（未能生成综述，可能未检索到相关论文）"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _agent_config(thread_id: str) -> dict:
    return {
        "recursion_limit": RECURSION_LIMIT,
        "callbacks": [HANDLER],
        "configurable": {"thread_id": thread_id},
    }


def _initial_state(req: ResearchRequest, session_id: str) -> dict:
    """新 invoke 的初始 state。

    注意：checkpointer 会把传入字段 MERGE 进已存在的 state。
    - 新 thread（首次）：整个 state 从这里开始
    - 旧 thread（续接）：保留 messages/papers/draft 等，新字段（depth/max_papers/query）覆盖
    """
    cap = DEPTH_CAP.get(req.depth, 20)
    return {
        "query": req.query,
        "session_id": session_id,
        "depth": req.depth,
        "max_papers": min(req.max_papers, cap),
    }


def _save_report(session_id: str, report: str) -> str:
    reports_dir = Path(settings.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{session_id}_{uuid.uuid4().hex[:8]}.md"
    path.write_text(report, encoding="utf-8")
    return str(path)


async def _finalize(session_id: str, agent, config: dict) -> tuple[str, str, list]:
    """从 checkpointer 读最终 state → 落盘报告 + 写 messages（侧边栏用）。"""
    snap = await agent.aget_state(config)
    state = snap.values if snap else {}
    draft = state.get("draft") or NO_REPORT
    papers = state.get("papers", []) or []
    report_path = _save_report(session_id, draft)
    # 写 messages 表（仅供侧边栏展示，不是 agent state 的一部分）
    query = state.get("query", "")
    if query:
        store.add_message(session_id, "user", query)
    store.add_message(session_id, "assistant", draft)
    return draft, report_path, papers


@router.post("", response_model=ResearchResponse)
async def start_research(req: ResearchRequest) -> ResearchResponse:
    """非流式：跑 agent 直到收工。"""
    session = store.get_or_create(req.session_id, mode="research")
    agent = build_agent()
    config = _agent_config(session.id)
    try:
        await agent.ainvoke(_initial_state(req, session.id), config=config)
    except GraphRecursionError:
        pass

    draft, report_path, papers = await _finalize(session.id, agent, config)
    return ResearchResponse(
        session_id=session.id,
        report_markdown=draft,
        report_path=report_path,
        papers=[PaperOut(**p) for p in papers],
    )


@router.post("/stream")
async def start_research_stream(req: ResearchRequest) -> StreamingResponse:
    """SSE 流式：每次 tool 调用推一条 step，末尾 final。"""
    session = store.get_or_create(req.session_id, mode="research")
    agent = build_agent()
    config = _agent_config(session.id)

    async def gen():
        try:
            async for chunk in agent.astream(
                _initial_state(req, session.id),
                stream_mode="updates",
                config=config,
            ):
                for update in (chunk or {}).values():
                    for m in (update or {}).get("messages", []):
                        for tc in getattr(m, "tool_calls", None) or []:
                            yield _sse("step", {"node": tc["name"]})
        except GraphRecursionError:
            pass
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"code": "internal_error", "message": str(e)})
            return

        draft, report_path, papers = await _finalize(session.id, agent, config)
        yield _sse(
            "final",
            {
                "session_id": session.id,
                "report_markdown": draft,
                "report_path": report_path,
                "papers": papers,
            },
        )
        # ponytail: 多发一个空 keep-alive 防 uvicorn/proxy 缓冲挂起
        yield ": done\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
