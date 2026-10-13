"""研究模式 API（M4.5.1：langgraph checkpointer + M5.5：plan 模块 + M5.5.6：多轮追问）。

- 每次 /api/research：构造 initial state dict + thread_id=session_id 调 agent
- checkpointer 自动按 thread_id 持久化整个 state（消息历史 + workflow 产物）
- 跨轮 "agent 接着" = 同一 thread_id 下次 invoke 自动恢复 state
- 多 session 物理隔离 = 不同 thread_id
- M5.5：/api/research/plan 起 plan 流程 → interrupt → 等用户审 → approve 续接
- M5.5.6：/api/research/{sid}/continue 追问 —— 跳过 planner，调 build_agent()
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from pydantic import ValidationError

from app.agents.plan_graph import build_plan_graph
from app.agents.research_agent import RECURSION_LIMIT, build_agent
from app.agents.schemas import (
    ApproveRequest,
    ClarifyRequest,
    ContinueRequest,
    PlanEditRequest,
    PlanRequest,
    PlanResponse,
    ResearchPlan,
)
from app.config import settings
from app.observability.callbacks import HANDLER
from app.schemas.research import PaperOut, ResearchRequest, ResearchResponse
from app.session_store import store
from app import research_runs
from app.observability.logging import logger

router = APIRouter(prefix="/api/research", tags=["research"])

# depth 档位 → max_papers 上限（§M3.3）
DEPTH_CAP = {"quick": 8, "normal": 20, "deep": 40}
NO_REPORT = "（未能生成综述，可能未检索到相关论文）"


def _validate_plan(plan: dict) -> dict:
    try:
        return ResearchPlan.model_validate(plan).model_dump()
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors(include_context=False)) from exc


def _agent_config(thread_id: str) -> RunnableConfig:
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
        "messages": [HumanMessage(content=req.query)],
    }


def _save_report(session_id: str, report: str) -> str:
    reports_dir = Path(settings.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{session_id}_{uuid.uuid4().hex[:8]}.md"
    path.write_text(report, encoding="utf-8")
    return str(path)


async def _finalize(
    session_id: str, agent: Any, config: RunnableConfig, *, query: str | None = None,
    draft_before: str | None = None, msg_count_before: int | None = None,
) -> tuple[str, str, list, str | None, str | None]:
    """从 checkpointer 读最终 state → 落盘报告 + 写 messages（侧边栏用）。

    M5.5.10: 返回 failure_reason 让前端能渲染失败提示：
    - 'search_failed'：state.papers 为空（arxiv 检索全部失败）
    - 'download_failed'：papers 有但 download_failures 全空（即所有 PDF 都拉不下来）
    - None：正常路径
    """
    snap = await agent.aget_state(config)
    state = snap.values if snap else {}
    draft = state.get("draft") or NO_REPORT
    # 先确定本轮回复，再统一写文件和消息，保证 SSE、历史与导出一致。
    if msg_count_before is not None and state.get("draft") == draft_before:
        for message in reversed(state.get("messages", [])[msg_count_before:]):
            if type(message).__name__ == "AIMessage" and message.content and not message.tool_calls:
                draft = message.text
                break
    papers = state.get("papers", []) or []
    download_failures = state.get("download_failures", []) or []

    # 推断失败原因
    failure_reason: str | None = None
    if not papers:
        failure_reason = "search_failed"
    elif download_failures and not any(p.get("local_path") for p in papers):
        failure_reason = "download_failed"

    report_path = _save_report(session_id, draft)
    # 写 messages 表（仅供侧边栏展示，不是 agent state 的一部分）
    query = state.get("query", "") if query is None else query
    if query:
        store.add_message(session_id, "user", query)
    message_id = store.add_message(session_id, "assistant", draft)
    return draft, report_path, papers, failure_reason, message_id


@router.post("", response_model=ResearchResponse)
async def start_research(req: ResearchRequest) -> ResearchResponse:
    """非流式：跑 agent 直到收工。"""
    session = store.get_or_create(req.session_id, mode="research")
    agent = build_agent()
    config = _agent_config(session.id)
    run = research_runs.start(session.id, lambda run: _run_research(
        session.id, run, agent, _initial_state(req, session.id), config))
    if run.task:
        await asyncio.shield(run.task)
    if run.error:
        raise HTTPException(500, run.error)
    final = next(data for event, data in run.events if event == "final")
    return ResearchResponse(
        session_id=session.id,
        report_markdown=final["report_markdown"],
        report_path=final["report_path"],
        papers=[PaperOut(**p) for p in final["papers"]],
        failure_reason=final["failure_reason"],
    )


async def _run_research(
    sid: str, run: research_runs.ResearchRun, agent: Any, initial: Any,
    config: RunnableConfig, **finalize_kwargs: Any,
) -> dict:
    seen: set[str] = set()
    async def emit_calls(messages: list) -> None:
        for message in messages:
            for call in getattr(message, "tool_calls", None) or []:
                if call["id"] in seen:
                    continue
                seen.add(call["id"])
                await run.emit("step", {"node": call["name"]})
                logger.info("research.step", session_id=sid, node=call["name"])

    if initial is None:
        snap = await agent.aget_state(config, subgraphs=True)
        if "researcher" in snap.next:
            snap = next(task.state for task in snap.tasks if task.name == "researcher")
        if "tools" in snap.next:
            for message in reversed(snap.values.get("messages", [])):
                if getattr(message, "tool_calls", None):
                    await emit_calls([message])
                    break
    async for namespace, chunk in agent.astream(
        initial, stream_mode="updates", subgraphs=True, config=config,
    ):
        for node, update in (chunk or {}).items():
            if node == "__interrupt__" or (not namespace and node == "researcher"):
                continue
            await emit_calls((update or {}).get("messages", []))
    draft, report_path, papers, failure_reason, message_id = await _finalize(sid, agent, config, **finalize_kwargs)
    return {"session_id": sid, "report_markdown": draft, "report_path": report_path,
            "papers": papers, "failure_reason": failure_reason, "message_id": message_id}


def _research_response(sid: str, agent: Any, initial: Any, config: RunnableConfig,
                       **finalize_kwargs: Any) -> StreamingResponse:
    run = research_runs.start(sid, lambda run: _run_research(
        sid, run, agent, initial, config, **finalize_kwargs))
    return StreamingResponse(research_runs.stream(sid, run), media_type="text/event-stream")


@router.post("/stream")
async def start_research_stream(req: ResearchRequest) -> StreamingResponse:
    session = store.get_or_create(req.session_id, mode="research")
    return _research_response(session.id, build_agent(), _initial_state(req, session.id),
                              _agent_config(session.id))


@router.get("/{sid}/events")
async def research_events(sid: str) -> StreamingResponse:
    if store.get(sid) is None:
        raise HTTPException(404, "会话不存在")
    run = research_runs.runs.get(sid)
    if run is None:
        raise HTTPException(409, "当前服务没有该研究任务，请刷新查看状态")
    return StreamingResponse(research_runs.stream(sid, run), media_type="text/event-stream")


# === M5.5 Plan API ===

async def _plan_initial_state(req: PlanRequest, session_id: str) -> dict:
    """plan 流程的初始 state。已有 plan 时不覆盖（让后续 POST /plan 仍是补生成）。"""
    cap = DEPTH_CAP.get(req.depth, 20)
    return {
        "query": req.query,
        "session_id": session_id,
        "depth": req.depth,
        "max_papers": min(req.max_papers, cap),
    }


async def _build_plan_graph():
    """构造 plan graph。checkpointer 单例。"""
    return build_plan_graph()


async def _execution_snapshot(graph: Any, config: RunnableConfig) -> tuple[Any, Any]:
    snap = await graph.aget_state(config)
    # Followups use the standalone graph; the plan graph cannot resolve its pending tools.
    if snap and snap.values.get("plan_status") == "approved" and not snap.next:
        agent = build_agent()
        agent_snap = await agent.aget_state(config)
        if agent_snap.next:
            return agent, agent_snap
    return graph, snap


@router.post("/plan", response_model=PlanResponse, status_code=202)
async def start_plan(req: PlanRequest) -> PlanResponse:
    """生成 plan：跑 planner node 触发 interrupt，返 plan + pending 状态。

    行为：
    - 新 session：planner 生成 plan → interrupt 暂停 → 兜底 aupdate_state 写 plan → 返 202
    - 已有 plan：planner 不会重新生成（state.plan 已存在），interrupt 再次弹出让用户重审
    - 已 approved（M5.5.6）：直接返当前 PlanResponse，不再弹 plan 卡片（追问走 /continue）

    ponytail：langgraph 的 interrupt() 抛 GraphInterrupt 时 node 不正常完成，
    return 的 update 不会被 checkpointer 自动持久化。所以 start_plan 在 interrupt
    抛出后显式 aupdate_state 把 plan 写进 state——保证 GET /{sid}/plan 立刻能拿到。
    """
    session = store.get_or_create(req.session_id, mode="research")
    config = _agent_config(session.id)
    graph = await _build_plan_graph()

    # M5.5.6 guard: 已 approved → 直接返当前状态，不跑 planner（追问该走 /continue）
    snap = await graph.aget_state(config)
    if snap and snap.values.get("plan_status") == "approved":
        s = snap.values
        return PlanResponse(
            session_id=session.id,
            plan=s.get("plan"),
            plan_status="approved",
            plan_generated_at=s.get("plan_generated_at"),
        )

    initial = await _plan_initial_state(req, session.id)
    research_runs.ensure_idle(session.id)

    # 跑直到 interrupt
    interrupted_payload: dict | None = None
    async for chunk in graph.astream(initial, config=config, stream_mode="updates"):
        if "__interrupt__" in (chunk or {}):
            interrupted_payload = chunk["__interrupt__"][0].value
            break

    # 兜底：把 plan 写入 state（langgraph 在 interrupt 时不会自动持久化 node update）
    if interrupted_payload and interrupted_payload.get("plan"):
        from datetime import datetime, timezone

        await graph.aupdate_state(
            config,
            {
                "plan": interrupted_payload["plan"],
                "plan_status": "pending",
                "plan_generated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    # 读最新 state
    snap = await graph.aget_state(config)
    s = snap.values if snap else {}
    if not s.get("plan"):
        raise HTTPException(500, "planner 未生成 plan")

    return PlanResponse(
        session_id=session.id,
        plan=s.get("plan"),
        plan_status=s.get("plan_status") or "pending",
        plan_generated_at=s.get("plan_generated_at"),
    )


@router.get("/{sid}/plan", response_model=PlanResponse)
async def get_plan(sid: str) -> PlanResponse:
    """读 checkpointer 里当前 plan 状态。"""
    graph = await _build_plan_graph()
    config = _agent_config(sid)
    _, snap = await _execution_snapshot(graph, config)
    if not snap:
        return PlanResponse(session_id=sid, plan_status="none")
    s = snap.values or {}
    run = research_runs.runs.get(sid)
    from app.db.models import SessionModel
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        session = db.get(SessionModel, sid)
        execution_status = session.status if session else "idle"
    error = run.error if run else None
    can_resume = s.get("plan_status") == "approved" and bool(snap.next) and not (run and not run.finished)
    if can_resume and execution_status != "failed":
        execution_status = "interrupted"
    if execution_status in {"failed", "interrupted"} and not error:
        error = "研究执行已中断，可从检查点继续" if can_resume else "研究执行失败，请查看后端日志"
    return PlanResponse(
        session_id=sid, plan=s.get("plan"), plan_status=s.get("plan_status") or "none",
        plan_generated_at=s.get("plan_generated_at"), execution_status=execution_status,
        execution_steps=[data["node"] for event, data in run.events if event == "step"] if run else [],
        execution_error=error, can_resume=can_resume,
    )


@router.patch("/{sid}/plan", response_model=PlanResponse)
async def edit_plan(sid: str, req: PlanEditRequest) -> PlanResponse:
    """用户编辑 plan（不批准执行，只更新字段）。

    ponytail：只覆盖客户端传的字段（merge），不删除其它 plan 字段；
    status 强制设为 "edited"。
    """
    research_runs.ensure_idle(sid)
    graph = await _build_plan_graph()
    config = _agent_config(sid)
    snap = await graph.aget_state(config)
    if not snap or not snap.values.get("plan"):
        raise HTTPException(404, "session 无 plan 可编辑")
    if snap.values.get("plan_status") not in {"pending", "edited"}:
        raise HTTPException(409, "只能编辑待审批计划")

    cur = dict(snap.values)
    merged = _validate_plan({**cur.get("plan", {}), **req.plan})
    await graph.aupdate_state(config, {"plan": merged, "plan_status": "edited"})

    return PlanResponse(
        session_id=sid,
        plan=merged,
        plan_status="edited",
        plan_generated_at=cur.get("plan_generated_at"),
    )


@router.post("/{sid}/plan/approve")
async def approve_plan(sid: str, req: ApproveRequest | None = None) -> StreamingResponse:
    """批准 plan → Command(resume=approve) → 续接跑 researcher（create_agent）→ SSE 推 step+final。"""
    research_runs.ensure_idle(sid)

    graph = await _build_plan_graph()
    config = _agent_config(sid)

    # 读当前 plan（可能有 PATCH 过的 edited_plan）
    snap = await graph.aget_state(config)
    if not snap or not snap.values.get("plan"):
        raise HTTPException(404, "session 无 plan 可批准")
    if snap.values.get("plan_status") not in {"pending", "edited"}:
        raise HTTPException(409, "计划已结束审批，请通过追问接口继续研究")
    cur_plan = snap.values.get("plan")

    # 优先用 req.edited_plan（PATCH 不调直接 approve 也可传）；否则用 state 当前 plan
    edited = _validate_plan(req.edited_plan) if req and req.edited_plan is not None else None

    decision: dict = {"action": "approve"}
    if edited:
        decision["edited_plan"] = edited
    elif snap.values.get("plan_status") == "edited":
        # PATCH 后未传 edited_plan → 用 state 当前 plan
        decision["edited_plan"] = cur_plan

    return _research_response(sid, graph, Command(resume=decision), config)


@router.post("/{sid}/plan/reject", response_model=PlanResponse)
async def reject_plan(sid: str) -> PlanResponse:
    """拒绝 plan → Command(resume=reject) → END，state 标 rejected。

    同时清空 plan 字段（让下次 POST /plan 能重新生成）。
    """
    research_runs.ensure_idle(sid)
    graph = await _build_plan_graph()
    config = _agent_config(sid)
    snap = await graph.aget_state(config)
    if not snap or not snap.values.get("plan"):
        raise HTTPException(404, "session 无 plan 可拒绝")
    if snap.values.get("plan_status") not in {"pending", "edited"}:
        raise HTTPException(409, "只能拒绝待审批计划")

    async for _ in graph.astream(
        Command(resume={"action": "reject"}), config=config
    ):
        pass

    # 清 plan 字段，避免下次 POST /plan 被 state.plan 残留拦截
    await graph.aupdate_state(config, {"plan": None, "plan_status": "rejected"})

    return PlanResponse(
        session_id=sid,
        plan=None,
        plan_status="rejected",
        plan_generated_at=snap.values.get("plan_generated_at"),
    )


# === M5.5.6 多轮追问 ===

@router.post("/{sid}/continue/stream")
async def continue_research_stream(
    sid: str, req: ContinueRequest
) -> StreamingResponse:
    """追问：跳过 planner，直接调 build_agent()，新 query 作为 HumanMessage 追加到 state.messages。

    - 校验 plan_status=='approved'，否则 409（未 plan / plan pending 时追问应先走 /plan）
    - checkpointer 共享：state 自动恢复（含 plan / draft / papers / 历史 messages）
    - input 追加 HumanMessage 和恢复上下文，不覆盖 query/depth/max_papers
    - agent 看到 history + 新 query，按 SYSTEM_PROMPT 意图识别调工具：
      "修改综述" → write_review；"再检索" → search_arxiv；"问答" → 不调工具
    - SSE 协议同 /plan/approve：step + final + :done
    - 末尾 _finalize 复用写 store 逻辑
    """
    research_runs.ensure_idle(sid)

    config = _agent_config(sid)

    # 1) 校验：plan_status 必须 approved
    graph = await _build_plan_graph()
    snap = await graph.aget_state(config)
    if not snap or snap.values.get("plan_status") != "approved":
        raise HTTPException(
            409, "session 未通过 plan，请先 POST /api/research/plan 并批准"
        )

    # 2) 直接调 build_agent()，共享 saver
    agent = build_agent()
    new_human = HumanMessage(content=req.query)

    # M5.5.11: 快照 agent 跑前的 draft + message count —— 若 agent 没调 write_review
    # （纯问答追问），draft 不变，以 agent 新增的最后一条 AIMessage 作为报告。
    draft_before = snap.values.get("draft") if snap else None
    msg_count_before = len(snap.values.get("messages", [])) if snap else 0

    _, execution_snap = await _execution_snapshot(graph, config)
    if execution_snap.next:
        raise HTTPException(409, "上次研究尚未完成，请先恢复执行")
    return _research_response(
        sid, agent, {"messages": [new_human], "run_query": req.query,
                     "run_draft_before": draft_before, "run_msg_count_before": msg_count_before},
        config, query=req.query,
        draft_before=draft_before, msg_count_before=msg_count_before,
    )


@router.post("/{sid}/resume/stream")
async def resume_research(sid: str) -> StreamingResponse:
    if store.get(sid) is None:
        raise HTTPException(404, "会话不存在")
    research_runs.ensure_idle(sid)
    graph = await _build_plan_graph()
    config = _agent_config(sid)
    agent, snap = await _execution_snapshot(graph, config)
    if not snap or snap.values.get("plan_status") != "approved" or not snap.next:
        raise HTTPException(409, "没有可恢复的已批准研究")
    kwargs = {}
    if agent is not graph and "run_query" in snap.values:
        kwargs = {"query": snap.values["run_query"],
                  "draft_before": snap.values.get("run_draft_before"),
                  "msg_count_before": snap.values.get("run_msg_count_before")}
    return _research_response(sid, agent, None, config, **kwargs)
