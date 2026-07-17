"""研究模式 API（M3.3 非流式；M3.4 加 SSE）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from app.agents.research_graph import graph
from app.config import settings
from app.schemas.research import PaperOut, ResearchRequest, ResearchResponse
from app.session_store import store

router = APIRouter(prefix="/research", tags=["research"])

# depth 档位 → max_papers 上限（§M3.3 depth 映射）
DEPTH_CAP = {"quick": 8, "normal": 20, "deep": 40}


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
