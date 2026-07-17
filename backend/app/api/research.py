"""研究模式 API（M3.3 非流式；M3.4 加 SSE）。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.agents.research_graph import graph
from app.config import settings
from app.errors import ScholarAIError
from app.schemas.research import PaperOut, ResearchRequest, ResearchResponse
from app.session_store import store

router = APIRouter(prefix="/research", tags=["research"])

# depth 档位 → max_papers 上限（§M3.3 depth 映射）
DEPTH_CAP = {"quick": 8, "normal": 20, "deep": 40}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _initial_state(req: ResearchRequest) -> dict:
    cap = DEPTH_CAP.get(req.depth, 20)
    return {
        "query": req.query,
        "depth": req.depth,
        "max_papers": min(req.max_papers, cap),
        "iteration": 0,
    }


def _save_report(session_id: str, report: str) -> str:
    reports_dir = Path(settings.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{session_id}.md"
    path.write_text(report, encoding="utf-8")
    return str(path)


@router.post("", response_model=ResearchResponse)
async def start_research(req: ResearchRequest) -> ResearchResponse:
    """非流式：一次跑完 planner→searcher→synthesizer，落盘综述。"""
    session = store.get_or_create(req.session_id, mode="research")
    final = await graph.ainvoke(_initial_state(req))
    report = final.get("report_draft", "")
    papers = final.get("papers", [])

    report_path = _save_report(session.id, report)
    store.add_message(session.id, "user", req.query)
    store.add_message(session.id, "assistant", report)

    return ResearchResponse(
        session_id=session.id,
        report_markdown=report,
        report_path=report_path,
        papers=[PaperOut(**p) for p in papers],
    )


@router.post("/stream")
async def start_research_stream(req: ResearchRequest) -> StreamingResponse:
    """SSE 流式：每个节点完成推一条 step，末尾 final 带完整综述。"""
    session = store.get_or_create(req.session_id, mode="research")

    async def gen():
        # ponytail: 不做 15s 心跳——dev 无空闲代理；生产上反代前置时再加
        state: dict = {}
        try:
            async for chunk in graph.astream(_initial_state(req), stream_mode="updates"):
                node = next(iter(chunk))
                state.update(chunk[node] or {})
                yield _sse("step", {"node": node, "status": "done"})
            report = state.get("report_draft", "")
            papers = state.get("papers", [])
            report_path = _save_report(session.id, report)
            store.add_message(session.id, "user", req.query)
            store.add_message(session.id, "assistant", report)
            yield _sse(
                "final",
                {
                    "session_id": session.id,
                    "report_markdown": report,
                    "report_path": report_path,
                    "papers": papers,
                },
            )
        except ScholarAIError as e:
            yield _sse("error", {"code": e.code, "message": e.message})
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"code": "internal_error", "message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")
