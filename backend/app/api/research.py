"""研究模式 API（M4.5：ReAct 主 agent 调度，替换固定流水线）。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langgraph.errors import GraphRecursionError

from app.agents.context import ResearchContext
from app.agents.research_agent import RECURSION_LIMIT, build_agent
from app.config import settings
from app.schemas.research import PaperOut, ResearchRequest, ResearchResponse
from app.session_store import store

router = APIRouter(prefix="/research", tags=["research"])

# depth 档位 → max_papers 上限（§M3.3 depth 映射）
DEPTH_CAP = {"quick": 8, "normal": 20, "deep": 40}
NO_REPORT = "（未能生成综述，可能未检索到相关论文）"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _new_context(req: ResearchRequest) -> ResearchContext:
    cap = DEPTH_CAP.get(req.depth, 20)
    return ResearchContext(query=req.query, depth=req.depth, max_papers=min(req.max_papers, cap))


def _save_report(session_id: str, report: str) -> str:
    reports_dir = Path(settings.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{session_id}.md"
    path.write_text(report, encoding="utf-8")
    return str(path)


def _finalize(session_id: str, ctx: ResearchContext) -> tuple[str, str]:
    report = ctx.draft or NO_REPORT
    report_path = _save_report(session_id, report)
    store.add_message(session_id, "user", ctx.query)
    store.add_message(session_id, "assistant", report)
    return report, report_path


@router.post("", response_model=ResearchResponse)
async def start_research(req: ResearchRequest) -> ResearchResponse:
    """非流式：跑 ReAct agent 直到收工，返回 ctx 里的综述与论文。"""
    session = store.get_or_create(req.session_id, mode="research")
    ctx = _new_context(req)
    agent = build_agent(ctx)
    try:
        await agent.ainvoke(
            {"messages": [("user", req.query)]}, config={"recursion_limit": RECURSION_LIMIT}
        )
    except GraphRecursionError:
        pass  # 到步数上限，用已生成的 draft 收尾

    report, report_path = _finalize(session.id, ctx)
    return ResearchResponse(
        session_id=session.id,
        report_markdown=report,
        report_path=report_path,
        papers=[PaperOut(**p) for p in ctx.papers],
    )


@router.post("/stream")
async def start_research_stream(req: ResearchRequest) -> StreamingResponse:
    """SSE 流式：每次 tool 调用推一条 step（动态时间线），末尾 final。"""
    session = store.get_or_create(req.session_id, mode="research")
    ctx = _new_context(req)
    agent = build_agent(ctx)

    async def gen():
        try:
            async for chunk in agent.astream(
                {"messages": [("user", req.query)]},
                stream_mode="updates",
                config={"recursion_limit": RECURSION_LIMIT},
            ):
                for update in (chunk or {}).values():
                    for m in (update or {}).get("messages", []):
                        for tc in getattr(m, "tool_calls", None) or []:
                            yield _sse("step", {"node": tc["name"]})
        except GraphRecursionError:
            pass  # 到步数上限，用已有 draft 收尾
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"code": "internal_error", "message": str(e)})

        report, report_path = _finalize(session.id, ctx)
        yield _sse(
            "final",
            {
                "session_id": session.id,
                "report_markdown": report,
                "report_path": report_path,
                "papers": ctx.papers,
            },
        )

    return StreamingResponse(gen(), media_type="text/event-stream")
