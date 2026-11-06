"""研究模式 API（意图澄清、计划审批、主 Agent 执行和会话追问）。

- 研究执行统一从澄清和已批准计划进入；旧的直接研究入口已移除
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
from app.agents.nodes.synthesizer import evidence_papers
from app.agents.intent import classify_request
from app.agents.research_agent import RECURSION_LIMIT, build_agent
from app.agents.research_conversation import build_agent as build_conversation_agent
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

# 研究模式 → max_papers 上限
MODE_CAP = {"quick": 8, "deep": 40}
NO_REPORT = "（未能生成综述，可能未检索到相关论文）"


def _research_mode(request: Any) -> str:
    """Return the explicit workflow mode; unspecified research defaults to quick."""
    return getattr(request, "research_mode", None) or "quick"


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
    - 旧 thread（续接）：保留 messages/papers/draft 等，新字段（mode/max_papers/query）覆盖
    """
    mode = _research_mode(req)
    cap = MODE_CAP.get(mode, 8)
    classification = classify_request(req.query)
    initial = {
        "query": req.query,
        "original_query": req.query,
        "clarified_query": req.query,
        "session_id": session_id,
        "intent": classification.intent,
        "research_mode": mode,
        "max_papers": min(req.max_papers, cap),
        "search_attempts": 0,
        "messages": [HumanMessage(content=req.query)],
    }
    return initial


def _save_report(session_id: str, report: str) -> str:
    reports_dir = Path(settings.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{session_id}_{uuid.uuid4().hex[:8]}.md"
    path.write_text(report, encoding="utf-8")
    return str(path)


async def _finalize(
    session_id: str, agent: Any, config: RunnableConfig, *, query: str | None = None,
    draft_before: str | None = None, msg_count_before: int | None = None,
    ) -> tuple[str, str, list, str | None, str | None, str]:
    """从 checkpointer 读最终 state → 落盘报告 + 写 messages（侧边栏用）。

    M5.5.10: 返回 failure_reason 让前端能渲染失败提示：
    - 'search_failed'：state.papers 为空（所有已启用来源均未返回论文）
    - 'download_failed'：所有 PDF 下载失败，且没有生成可用的摘要综述
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

    has_evidence_report = bool(state.get("draft") and evidence_papers(state))
    # 已用下载失败论文的摘要完成综述时，下载失败不再代表整次研究失败。
    failure_reason: str | None = None
    if not papers:
        failure_reason = "search_failed"
    elif download_failures and not any(p.get("local_path") for p in papers) and not has_evidence_report:
        failure_reason = "download_failed"
    elif state.get("research_mode") == "deep" and not has_evidence_report and not any(
        item.get("status") != "failed" for item in state.get("analyses", [])
    ):
        failure_reason = "analysis_failed"
    elif state.get("review_status") == "failed":
        failure_reason = "review_failed"

    report_path = _save_report(session_id, draft)
    # 写 messages 表（仅供侧边栏展示，不是 agent state 的一部分）
    query = state.get("query", "") if query is None else query
    if query:
        store.add_message(session_id, "user", query)
    message_id = store.add_message(session_id, "assistant", draft)
    return draft, report_path, papers, failure_reason, message_id, "needs_review" if failure_reason else "done"


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
    draft, report_path, papers, failure_reason, message_id, session_status = await _finalize(
        sid, agent, config, **finalize_kwargs
    )
    return {"session_id": sid, "report_markdown": draft, "report_path": report_path,
            "papers": papers, "failure_reason": failure_reason, "message_id": message_id,
            "_session_status": session_status}


def _research_response(sid: str, agent: Any, initial: Any, config: RunnableConfig,
                       *, existing_run: research_runs.ResearchRun | None = None,
                       **finalize_kwargs: Any) -> StreamingResponse:
    run = research_runs.start(sid, lambda run: _run_research(
        sid, run, agent, initial, config, **finalize_kwargs), existing=existing_run)
    return StreamingResponse(research_runs.stream(sid, run), media_type="text/event-stream")


@router.get("/{sid}/events")
async def research_events(sid: str) -> StreamingResponse:
    session = store.get(sid)
    if session is None:
        raise HTTPException(404, "会话不存在")
    if session.mode != "research":
        raise HTTPException(409, "会话不是研究模式")
    run = research_runs.runs.get(sid)
    if run is None:
        raise HTTPException(409, "当前服务没有该研究任务，请刷新查看状态")
    return StreamingResponse(research_runs.stream(sid, run), media_type="text/event-stream")


# === M5.5 Plan API ===

async def _plan_initial_state(
    req: PlanRequest, session_id: str, existing_state: dict[str, Any] | None = None
) -> dict:
    """plan 流程的初始 state。

    计划阶段只保留意图和研究参数；来源选择由 paper-search-mcp 统一负责。
    """
    existing = existing_state or {}
    # Preserve a mode explicitly chosen during intent clarification.  A plan
    # request defaults to quick, so only an explicit deep request should
    # override an already clarified mode.
    existing_mode = existing.get("research_mode")
    mode = "deep" if req.research_mode == "deep" else (existing_mode or "quick")
    cap = MODE_CAP.get(mode, 8)
    classification = classify_request(req.query)
    initial = {
        "query": req.query,
        "original_query": existing.get("original_query") or req.query,
        "clarified_query": req.query,
        "session_id": session_id,
        "intent": existing.get("intent") or classification.intent,
        "research_mode": mode,
        "max_papers": min(req.max_papers, cap),
        "search_attempts": 0,
    }
    # The planner itself only needs state.query, but the approved plan resumes
    # the researcher create_agent. It requires at least one user message; keep
    # the original query in history for new plan sessions so approval can enter
    # the research chain with a valid chat transcript.
    if not existing.get("messages"):
        initial["messages"] = [HumanMessage(content=req.query)]
    return initial


async def _build_plan_graph():
    """构造 plan graph。checkpointer 单例。"""
    return build_plan_graph()


async def _run_plan(graph: Any, initial: dict, config: RunnableConfig) -> dict:
    """Run planner in an application-owned task until its approval interrupt."""
    interrupted_payload: dict | None = None
    async for chunk in graph.astream(initial, config=config, stream_mode="updates"):
        if "__interrupt__" in (chunk or {}):
            interrupted_payload = chunk["__interrupt__"][0].value
            break

    if interrupted_payload and interrupted_payload.get("plan"):
        from datetime import datetime, timezone

        await graph.aupdate_state(
            config,
            {
                "plan": interrupted_payload["plan"],
                "research_mode": interrupted_payload["plan"].get("research_mode", "quick"),
                "plan_status": "pending",
                "plan_generated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    snap = await graph.aget_state(config)
    state = snap.values if snap else {}
    if not state.get("plan"):
        raise HTTPException(500, "planner 未生成 plan")
    return {
        "session_id": config["configurable"]["thread_id"],
        "plan": state["plan"],
        "plan_status": state.get("plan_status") or "pending",
        "plan_generated_at": state.get("plan_generated_at"),
    }


async def _execution_snapshot(graph: Any, config: RunnableConfig) -> tuple[Any, Any]:
    snap = await graph.aget_state(config)
    # Followups use the standalone graph; the plan graph cannot resolve its pending tools.
    if snap and snap.values.get("plan_status") == "approved" and not snap.next:
        agent = (
            build_conversation_agent()
            if snap.values.get("intent") in {"chat", "followup"}
            else build_agent()
        )
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
    research_runs.ensure_idle(session.id)
    graph = await _build_plan_graph()

    # M5.5.6 guard: 已 approved → 直接返当前状态，不跑 planner（追问该走 /continue）
    snap = await graph.aget_state(config)
    if not snap or not snap.values.get("clarify_direction"):
        raise HTTPException(409, "请先完成研究意图澄清，再生成研究计划")
    if snap and snap.values.get("plan_status") == "approved":
        s = snap.values
        return PlanResponse(
            session_id=session.id,
            plan=s.get("plan"),
            plan_status="approved",
            plan_generated_at=s.get("plan_generated_at"),
        )

    initial = await _plan_initial_state(req, session.id, snap.values if snap else None)
    run = research_runs.start(
        session.id,
        lambda _: _run_plan(graph, initial, config),
        kind="plan",
        success_status="idle",
        final_event=None,
    )
    if run.task:
        await asyncio.shield(run.task)
    if run.error:
        status = 504 if run.error_code == "research_timeout" else 500
        raise HTTPException(status, run.error)
    result = run.result or {}
    return PlanResponse(**result)


@router.get("/{sid}/plan", response_model=PlanResponse)
async def get_plan(sid: str) -> PlanResponse:
    """读 checkpointer 里当前 plan 状态。"""
    session_info = store.get(sid)
    if session_info is not None and session_info.mode != "research":
        raise HTTPException(409, "会话不是研究模式")
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
    if can_resume and execution_status in {"idle", "done"}:
        execution_status = "interrupted"
    if execution_status in {"failed", "interrupted", "timed_out", "cancelled", "needs_review"} and not error:
        if execution_status == "timed_out":
            error = "研究超过时间限制，可从检查点继续" if can_resume else "研究超过时间限制"
        elif execution_status == "cancelled":
            error = "研究已取消"
        elif execution_status == "needs_review":
            error = "研究已生成，但结果需要人工复核"
        else:
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
    run = research_runs.claim(sid, "plan_edit")
    try:
        graph = await _build_plan_graph()
        config = _agent_config(sid)
        snap = await graph.aget_state(config)
        if not snap or not snap.values.get("plan"):
            raise HTTPException(404, "session 无 plan 可编辑")
        if snap.values.get("plan_status") not in {"pending", "edited"}:
            raise HTTPException(409, "只能编辑待审批计划")

        cur = dict(snap.values)
        merged = _validate_plan({**cur.get("plan", {}), **req.plan})
        await graph.aupdate_state(config, {
            "plan": merged,
            "research_mode": merged.get("research_mode", cur.get("research_mode", "quick")),
            "plan_status": "edited",
        })
    except Exception:
        research_runs.finish_claim(run)
        raise
    research_runs.finish_claim(run)

    return PlanResponse(
        session_id=sid,
        plan=merged,
        plan_status="edited",
        plan_generated_at=cur.get("plan_generated_at"),
    )


@router.post("/{sid}/plan/approve")
async def approve_plan(sid: str, req: ApproveRequest | None = None) -> StreamingResponse:
    """批准 plan → Command(resume=approve) → 续接跑 researcher（create_agent）→ SSE 推 step+final。"""
    run = research_runs.claim(sid, "execute", start_status="running")
    try:
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
            decision["edited_plan"] = cur_plan

        return _research_response(
            sid, graph, Command(resume=decision), config, existing_run=run
        )
    except Exception:
        research_runs.finish_claim(run)
        raise


@router.post("/{sid}/plan/reject", response_model=PlanResponse)
async def reject_plan(sid: str) -> PlanResponse:
    """拒绝 plan → Command(resume=reject) → END，state 标 rejected。

    同时清空 plan 字段（让下次 POST /plan 能重新生成）。
    """
    run = research_runs.claim(sid, "plan_reject")
    try:
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
    except Exception:
        research_runs.finish_claim(run)
        raise
    research_runs.finish_claim(run)

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
    - input 追加 HumanMessage 和恢复上下文，不覆盖研究计划和论文结果
    - agent 看到 history + 新 query，按 SYSTEM_PROMPT 意图识别调工具：
      "修改综述" → write_review；"再检索" → search_papers；"问答" → 不调工具
    - SSE 协议同 /plan/approve：step + final + :done
    - 末尾 _finalize 复用写 store 逻辑
    """
    if store.get(sid) is None:
        raise HTTPException(409, "session 未通过 plan，请先 POST /api/research/plan 并批准")
    run = research_runs.claim(sid, "execute", start_status="running")
    try:
        config = _agent_config(sid)

        # 1) 校验：plan_status 必须 approved
        graph = await _build_plan_graph()
        snap = await graph.aget_state(config)
        if not snap or snap.values.get("plan_status") != "approved":
            raise HTTPException(
                409, "session 未通过 plan，请先 POST /api/research/plan 并批准"
            )

        # 2) 研究会话中的普通追问走无研究工具的对话 Agent；明确新研究请求
        # 才继续使用研究工具 Agent。
        classification = classify_request(req.query, has_research_context=True)
        agent = build_conversation_agent() if classification.intent in {"chat", "followup"} else build_agent()
        new_human = HumanMessage(content=req.query)

        # M5.5.11: 快照 agent 跑前的 draft + message count —— 若 agent 没调 write_review
        # （纯问答追问），draft 不变，以 agent 新增的最后一条 AIMessage 作为报告。
        draft_before = snap.values.get("draft") if snap else None
        msg_count_before = len(snap.values.get("messages", [])) if snap else 0

        _, execution_snap = await _execution_snapshot(graph, config)
        if execution_snap.next:
            raise HTTPException(409, "上次研究尚未完成，请先恢复执行")
        return _research_response(
            sid, agent, {"messages": [new_human], "intent": classification.intent,
                         "search_attempts": 0, "run_query": req.query,
                         "run_draft_before": draft_before, "run_msg_count_before": msg_count_before},
            config, query=req.query,
            draft_before=draft_before, msg_count_before=msg_count_before,
            existing_run=run,
        )
    except Exception:
        research_runs.finish_claim(run)
        raise


@router.post("/{sid}/resume/stream")
async def resume_research(sid: str) -> StreamingResponse:
    if store.get(sid) is None:
        raise HTTPException(404, "会话不存在")
    run = research_runs.claim(sid, "execute", start_status="running")
    try:
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
        return _research_response(sid, agent, None, config, existing_run=run, **kwargs)
    except Exception:
        research_runs.finish_claim(run)
        raise


@router.post("/{sid}/cancel")
async def cancel_research(sid: str) -> dict[str, str]:
    session = store.get(sid)
    if session is None:
        raise HTTPException(404, "会话不存在")
    if session.mode != "research":
        raise HTTPException(409, "会话不是研究模式")
    run = research_runs.runs.get(sid)
    if not run or run.finished or not run.task:
        raise HTTPException(409, "当前没有正在运行的研究")
    await research_runs.cancel(sid, reason="user", discard=False)
    return {"session_id": sid, "status": "cancelled"}
