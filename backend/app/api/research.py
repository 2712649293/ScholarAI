"""研究模式 API（M4.5.1：langgraph checkpointer + M5.5：plan 模块 + M5.5.6：多轮追问）。

- 每次 /api/research：构造 initial state dict + thread_id=session_id 调 agent
- checkpointer 自动按 thread_id 持久化整个 state（消息历史 + workflow 产物）
- 跨轮 "agent 接着" = 同一 thread_id 下次 invoke 自动恢复 state
- 多 session 物理隔离 = 不同 thread_id
- M5.5：/api/research/plan 起 plan 流程 → interrupt → 等用户审 → approve 续接
- M5.5.6：/api/research/{sid}/continue 追问 —— 跳过 planner，调 build_agent()
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from app.agents.plan_graph import build_plan_graph
from app.agents.research_agent import RECURSION_LIMIT, build_agent
from app.agents.schemas import (
    ApproveRequest,
    ContinueRequest,
    PlanEditRequest,
    PlanRequest,
    PlanResponse,
)
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


async def _finalize(
    session_id: str, agent, config: dict
) -> tuple[str, str, list, str | None]:
    """从 checkpointer 读最终 state → 落盘报告 + 写 messages（侧边栏用）。

    M5.5.10: 返回 failure_reason 让前端能渲染失败提示：
    - 'search_failed'：state.papers 为空（arxiv 检索全部失败）
    - 'download_failed'：papers 有但 download_failures 全空（即所有 PDF 都拉不下来）
    - None：正常路径
    """
    snap = await agent.aget_state(config)
    state = snap.values if snap else {}
    draft = state.get("draft") or NO_REPORT
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
    query = state.get("query", "")
    if query:
        store.add_message(session_id, "user", query)
    store.add_message(session_id, "assistant", draft)
    return draft, report_path, papers, failure_reason


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

    draft, report_path, papers, failure_reason = await _finalize(session.id, agent, config)
    return ResearchResponse(
        session_id=session.id,
        report_markdown=draft,
        report_path=report_path,
        papers=[PaperOut(**p) for p in papers],
        failure_reason=failure_reason,
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

        draft, report_path, papers, failure_reason = await _finalize(session.id, agent, config)
        yield _sse(
            "final",
            {
                "session_id": session.id,
                "report_markdown": draft,
                "report_path": report_path,
                "papers": papers,
                "failure_reason": failure_reason,  # M5.5.10
            },
        )
        # ponytail: 多发一个空 keep-alive 防 uvicorn/proxy 缓冲挂起
        yield ": done\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


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
    snap = await graph.aget_state(config)
    if not snap:
        return PlanResponse(session_id=sid, plan_status="none")
    s = snap.values or {}
    return PlanResponse(
        session_id=sid,
        plan=s.get("plan"),
        plan_status=s.get("plan_status") or "none",
        plan_generated_at=s.get("plan_generated_at"),
    )


@router.patch("/{sid}/plan", response_model=PlanResponse)
async def edit_plan(sid: str, req: PlanEditRequest) -> PlanResponse:
    """用户编辑 plan（不批准执行，只更新字段）。

    ponytail：只覆盖客户端传的字段（merge），不删除其它 plan 字段；
    status 强制设为 "edited"。
    """
    graph = await _build_plan_graph()
    config = _agent_config(sid)
    snap = await graph.aget_state(config)
    if not snap or not snap.values.get("plan"):
        raise HTTPException(404, "session 无 plan 可编辑")

    cur = dict(snap.values)
    merged = {**cur.get("plan", {}), **req.plan}
    cur["plan"] = merged
    cur["plan_status"] = "edited"
    await graph.aupdate_state(config, cur)

    return PlanResponse(
        session_id=sid,
        plan=merged,
        plan_status="edited",
        plan_generated_at=cur.get("plan_generated_at"),
    )


@router.post("/{sid}/plan/approve")
async def approve_plan(sid: str, req: ApproveRequest | None = None) -> StreamingResponse:
    """批准 plan → Command(resume=approve) → 续接跑 researcher（create_agent）→ SSE 推 step+final。"""
    graph = await _build_plan_graph()
    config = _agent_config(sid)

    # 读当前 plan（可能有 PATCH 过的 edited_plan）
    snap = await graph.aget_state(config)
    if not snap or not snap.values.get("plan"):
        raise HTTPException(404, "session 无 plan 可批准")
    cur_plan = snap.values.get("plan")

    # 优先用 req.edited_plan（PATCH 不调直接 approve 也可传）；否则用 state 当前 plan
    edited = req.edited_plan if req else None

    decision: dict = {"action": "approve"}
    if edited:
        decision["edited_plan"] = edited
    elif snap.values.get("plan_status") == "edited":
        # PATCH 后未传 edited_plan → 用 state 当前 plan
        decision["edited_plan"] = cur_plan

    async def gen():
        try:
            async for chunk in graph.astream(
                Command(resume=decision), config=config, stream_mode="updates"
            ):
                if "__interrupt__" in (chunk or {}):
                    # 不应再触发 interrupt；跳过
                    continue
                for update in (chunk or {}).values():
                    for m in (update or {}).get("messages", []):
                        for tc in getattr(m, "tool_calls", None) or []:
                            yield _sse("step", {"node": tc["name"]})
        except GraphRecursionError:
            pass
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"code": "internal_error", "message": str(e)})
            return

        # 读最终 state（researcher 跑完 → plan_status 应为 approved）
        draft, report_path, papers, failure_reason = await _finalize(sid, graph, config)
        yield _sse(
            "final",
            {
                "session_id": sid,
                "report_markdown": draft,
                "report_path": report_path,
                "papers": papers,
                "failure_reason": failure_reason,  # M5.5.10
            },
        )
        yield ": done\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/{sid}/plan/reject", response_model=PlanResponse)
async def reject_plan(sid: str) -> PlanResponse:
    """拒绝 plan → Command(resume=reject) → END，state 标 rejected。

    同时清空 plan 字段（让下次 POST /plan 能重新生成）。
    """
    graph = await _build_plan_graph()
    config = _agent_config(sid)
    snap = await graph.aget_state(config)
    if not snap:
        raise HTTPException(404, "session 不存在")

    async for _ in graph.astream(
        Command(resume={"action": "reject"}), config=config
    ):
        pass

    # 清 plan 字段，避免下次 POST /plan 被 state.plan 残留拦截
    cur = dict(snap.values)
    cur["plan"] = None
    cur["plan_status"] = "rejected"
    await graph.aupdate_state(config, cur)

    return PlanResponse(
        session_id=sid,
        plan=None,
        plan_status="rejected",
        plan_generated_at=cur.get("plan_generated_at"),
    )


# === M5.5.6 多轮追问 ===

@router.post("/{sid}/continue/stream")
async def continue_research_stream(
    sid: str, req: ContinueRequest
) -> StreamingResponse:
    """追问：跳过 planner，直接调 build_agent()，新 query 作为 HumanMessage 追加到 state.messages。

    - 校验 plan_status=='approved'，否则 409（未 plan / plan pending 时追问应先走 /plan）
    - checkpointer 共享：state 自动恢复（含 plan / draft / papers / 历史 messages）
    - input 只传 {"messages": [HumanMessage(query)]}，不覆盖 query/depth/max_papers
    - agent 看到 history + 新 query，按 SYSTEM_PROMPT 意图识别调工具：
      "修改综述" → write_review；"再检索" → search_arxiv；"问答" → 不调工具
    - SSE 协议同 /plan/approve：step + final + :done
    - 末尾 _finalize 复用写 store 逻辑
    """
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

    # M5.5.11: 快照 agent 跑前的 draft —— 若 agent 没调 write_review（纯问答追问），
    # draft 不变，以 agent 最后一条 AIMessage 作为报告（而非重推旧草稿）。
    draft_before = snap.values.get("draft") if snap else None

    async def gen():
        try:
            async for chunk in agent.astream(
                {"messages": [new_human]},
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

        draft, report_path, papers, failure_reason = await _finalize(sid, agent, config)

        # M5.5.11: draft 没变 → 纯问答 → 用 agent 最新回复而非旧草稿
        if not draft_before or draft == draft_before:
            # 取最后一条 AIMessage 内容作为回复
            msg_snap = await agent.aget_state(config)
            last_ai = None
            for m in reversed(msg_snap.values.get("messages", []) if msg_snap else []):
                if hasattr(m, "content") and not getattr(m, "tool_calls", None):
                    last_ai = m.content
                    break
            if last_ai and last_ai != draft:
                yield _sse(
                    "final",
                    {
                        "session_id": sid,
                        "report_markdown": last_ai,
                        "report_path": report_path,
                        "papers": papers,
                        "failure_reason": failure_reason,
                    },
                )
                yield ": done\n\n"
                return

        yield _sse(
            "final",
            {
                "session_id": sid,
                "report_markdown": draft,
                "report_path": report_path,
                "papers": papers,
                "failure_reason": failure_reason,
            },
        )
        yield ": done\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
