"""研究模式 API（M4.5：ReAct 主 agent 调度 + M4.6：跨轮记忆）。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langgraph.errors import GraphRecursionError

from app.agents.context import ResearchContext
from app.agents.research_agent import RECURSION_LIMIT, build_agent
from app.config import settings
from app.observability.callbacks import HANDLER
from app.schemas.research import PaperOut, ResearchRequest, ResearchResponse
from app.session_store import store

router = APIRouter(prefix="/api/research", tags=["research"])

# depth 档位 → max_papers 上限（§M3.3 depth 映射）
DEPTH_CAP = {"quick": 8, "normal": 20, "deep": 40}
NO_REPORT = "（未能生成综述，可能未检索到相关论文）"
_AGENT_CONFIG = {"recursion_limit": RECURSION_LIMIT, "callbacks": [HANDLER]}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _new_context(req: ResearchRequest, session_id: str) -> ResearchContext:
    cap = DEPTH_CAP.get(req.depth, 20)
    return ResearchContext(
        query=req.query,
        session_id=session_id,
        depth=req.depth,
        max_papers=min(req.max_papers, cap),
    )


def _load_prior_state(session_id: str, fallback_query: str, fallback_depth: str) -> ResearchContext:
    """读上次研究状态；无则新建。session 不存在或 mode 不匹配照样新建（兜底）。"""
    prior = store.load_research_state(session_id)
    if prior is None:
        return ResearchContext(query=fallback_query, session_id=session_id, depth=fallback_depth)
    return ResearchContext(
        query=fallback_query,
        session_id=session_id,
        depth=fallback_depth,
        sub_questions=prior.get("sub_questions", []),
        search_queries=prior.get("search_queries", []),
        papers=prior.get("papers", []),
        analyses=prior.get("analyses", []),
        draft=prior.get("draft", ""),
        feedback=prior.get("feedback", ""),
        download_failures=prior.get("download_failures", []),
        iteration=prior.get("iteration", 0),
    )


def _build_initial_messages(ctx: ResearchContext) -> list[tuple[str, str]]:
    """构造 agent 初始 messages：有前态就注 system note 说明续接上下文。"""
    msgs: list[tuple[str, str]] = []
    if ctx.papers or ctx.draft:
        # 续接模式：告诉 agent 上次进展，避免它把"接着"理解成"重做"
        paper_ids = [p.get("arxiv_id", "?") for p in ctx.papers[:20]]
        note = (
            "[续接提示] 这是研究会话的续接请求。\n"
            f"- 已检索 {len(ctx.papers)} 篇论文: {', '.join(paper_ids)}\n"
            f"- 已拆子问题: {ctx.sub_questions[:5] if ctx.sub_questions else '（无）'}\n"
            f"- 当前综述长度: {len(ctx.draft)} 字\n"
            f"如有 paper 列表已覆盖需求，直接进入 analyze/write_review；"
            f"如需新论文，可继续 search_arxiv 与已有论文去重累积。"
        )
        msgs.append(("system", note))
    msgs.append(("user", ctx.query))
    return msgs


def _save_report(session_id: str, report: str) -> str:
    reports_dir = Path(settings.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    # 唯一后缀：同一 session 多次研究不互相覆盖（历史都留档）
    path = reports_dir / f"{session_id}_{uuid.uuid4().hex[:8]}.md"
    path.write_text(report, encoding="utf-8")
    return str(path)


def _finalize(session_id: str, ctx: ResearchContext) -> tuple[str, str]:
    """写报告 + 消息 + 跨轮记忆（§12.11）。"""
    report = ctx.draft or NO_REPORT
    report_path = _save_report(session_id, report)
    store.add_message(session_id, "user", ctx.query)
    store.add_message(session_id, "assistant", report)
    # 持久化跨轮状态（最后研究胜出；下次同 session 请求会 load 进来）
    store.save_research_state(
        session_id,
        {
            "papers": ctx.papers,
            "analyses": ctx.analyses,
            "draft": ctx.draft,
            "feedback": ctx.feedback,
            "download_failures": ctx.download_failures,
            "search_queries": ctx.search_queries,
            "sub_questions": ctx.sub_questions,
            "iteration": ctx.iteration,
        },
    )
    return report, report_path


@router.post("", response_model=ResearchResponse)
async def start_research(req: ResearchRequest) -> ResearchResponse:
    """非流式：跑 ReAct agent 直到收工，返回 ctx 里的综述与论文。"""
    session = store.get_or_create(req.session_id, mode="research")
    ctx = (
        _load_prior_state(session.id, req.query, req.depth)
        if req.session_id
        else _new_context(req, session.id)
    )
    agent = build_agent(ctx)
    try:
        await agent.ainvoke(
            {"messages": _build_initial_messages(ctx)}, config=_AGENT_CONFIG
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
    ctx = (
        _load_prior_state(session.id, req.query, req.depth)
        if req.session_id
        else _new_context(req, session.id)
    )
    agent = build_agent(ctx)

    async def gen():
        try:
            async for chunk in agent.astream(
                {"messages": _build_initial_messages(ctx)},
                stream_mode="updates",
                config=_AGENT_CONFIG,
            ):
                for update in (chunk or {}).values():
                    for m in (update or {}).get("messages", []):
                        for tc in getattr(m, "tool_calls", None) or []:
                            yield _sse("step", {"node": tc["name"]})
        except GraphRecursionError:
            pass  # 到步数上限，用已有 draft 收尾
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"code": "internal_error", "message": str(e)})
            return  # 硬错误：只报错，不再发 final（避免前端错误+空综述双显示）

        report, report_path = _finalize(session.id, ctx)
        payload = {
            "session_id": session.id,
            "report_markdown": report,
            "report_path": report_path,
            "papers": ctx.papers,
        }
        yield _sse("final", payload)
        # ponytail: 多发一个空 keep-alive 防 uvicorn/proxy 缓冲挂起
        yield ": done\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
