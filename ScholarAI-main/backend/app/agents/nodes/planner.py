"""M5.5 · Planner 节点。

- generate_plan(): LLM 生成结构化 ResearchPlan
  - 主路径：langchain with_structured_output(ResearchPlan)
  - Fallback：JSON mode（response_format=json_object）+ Pydantic 校验
- planner_node(): LangGraph 节点
  - 首次进入（state.plan 为空）：生成 plan，写入 state.plan / plan_status="pending"
  - resume 时（interrupt() 返回用户决议）：
    - reject → plan_status="rejected"
    - approve（可带 edited_plan）→ plan_status="approved"，同步 sub_questions / search_queries 给后续 researcher 用

**关键约束**：interrupt() 必须在纯 LLM node 里调（不在 tool 里），否则 resume 重跑副作用。
planner_node 是纯 LLM 调用 + state 字段更新，无副作用，安全。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt

from app.agents.schemas import ResearchPlan
from app.agents.state import ResearchState
from app.llm import get_llm

PLANNER_SYSTEM = """你是学术研究规划师。基于用户给出的研究方向，输出结构化研究计划 JSON：

{
  "title": "综述标题（≤30字）",
  "sub_questions": [{"question": "...", "rationale": "..."}, ...],
  "search_queries": [{"intent": "...", "queries": ["...", "..."]}, ...],
  "outline": [{"heading": "...", "bullets": ["...", "..."]}, ...],
  "estimated_papers": 20,
  "reasoning": "1-3 句说明取舍"
}

约束：
- sub_questions 3-5 个，之间正交不重叠
- search_queries 3-5 组，每组 2-5 个英文短语（arxiv 风格，5-8 词）
- outline 5-7 个章节，每章节 1-4 个 bullet（bullet 是要写进综述的具体论点，不是空标题）
- estimated_papers 范围 5-50
- 只输出 JSON，不要任何额外文字或 markdown"""


async def generate_plan(query: str, depth: str = "normal") -> ResearchPlan:
    """调 LLM 生成 ResearchPlan。失败自动 fallback 到 JSON mode。

    ponytail：未加重试/tenacity — 一次失败直接走 fallback 路径。
    """
    msgs = [
        SystemMessage(content=PLANNER_SYSTEM),
        HumanMessage(content=f"研究方向：{query}\n深度档位：{depth}"),
    ]
    try:
        llm = get_llm().with_structured_output(ResearchPlan)
        result = await llm.ainvoke(msgs)
        if isinstance(result, ResearchPlan):
            return result
        # 某些 provider 返 dict
        return ResearchPlan.model_validate(result)
    except Exception:
        # Fallback：JSON mode + Pydantic 校验
        llm = get_llm().bind(response_format={"type": "json_object"})
        raw = await llm.ainvoke(msgs)
        content = raw.content if hasattr(raw, "content") else str(raw)
        return ResearchPlan.model_validate_json(content)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _planner_payload_for_client(plan: dict, session_id: str) -> dict:
    """interrupt() 推给前端看的 payload。"""
    return {"plan": plan, "session_id": session_id}


async def planner_node(state: ResearchState) -> dict:
    """Plan graph 的 planner 节点。

    首次进入 → 生成 plan + 触发 interrupt
    resume 时 → 根据用户决议更新 plan_status（approved / rejected）
    """
    # === 首次：生成 plan ===
    if not state.get("plan"):
        plan = await generate_plan(state["query"], state.get("depth", "normal"))
        # 关键顺序：return 在 interrupt 之前。langgraph 设计：
        # - node 返回 dict 是 update，会被 checkpointer 持久化
        # - interrupt() 抛 GraphInterrupt 暂停 graph，但 update 已被记录
        # - resume 时整个 node 重跑（"re-executing all logic"），state.get("plan")
        #   已存在 → 走下面 resume 分支
        # 这保证 GET /{sid}/plan 立刻能拿到（前端能渲染卡片 + 调 patch）。
        # 注：interrupt 必须在 return 之前——否则 return 没机会执行就被中断。
        # 但我们用 interrupt() 抛 GraphInterrupt 后，**整个 node 不再 return**，
        # 所以 update 走"node 完成前抛出"路径：langgraph 把 node 已执行的写入
        # （含 callable 调用产生的 dict）作为 update 持久化。
        payload = _planner_payload_for_client(plan.model_dump(), state.get("session_id", ""))
        # 用变量 + 显式 raise 模式——plan 写到 local，interrupt 后函数不 return，
        # 但 langgraph runtime 会通过 other mechanism 持久化 state（plan = checkpointer
        # 在 interrupt 时回写 input state 的方式）。
        # 实际我们靠的是：**state 字段 + interrupt 触发前 node 已执行的 side effect**
        # ——但这不可靠，所以改用 aupdate_state 兜底：在 start_plan API 调一次。
        _ = interrupt(payload)
        return {  # pragma: no cover
            "plan": plan.model_dump(),
            "plan_status": "pending",
            "plan_generated_at": _now_iso(),
        }

    # === resume：拿用户决议 ===
    decision = interrupt(_planner_payload_for_client(state["plan"], state.get("session_id", "")))
    # decision = {"action": "approve"|"reject", "edited_plan": {...} 可选}

    if not isinstance(decision, dict):
        # 容错：非法决议当 reject
        return {"plan_status": "rejected"}

    action = decision.get("action")
    if action == "reject":
        return {"plan_status": "rejected"}

    # approve：可选 edited_plan
    plan = decision.get("edited_plan") or state["plan"]
    # 提取 sub_questions 字符串列表（researcher agent 用）和 search_queries 字符串列表
    sub_q = [sq.get("question", "") if isinstance(sq, dict) else sq for sq in plan.get("sub_questions", [])]
    sq_groups = plan.get("search_queries", [])
    search_q = []
    for g in sq_groups:
        if isinstance(g, dict):
            search_q.extend(g.get("queries", []))
        elif isinstance(g, list):
            search_q.extend(g)
    # M5.5.8: 把 plan 内容作为 AIMessage 注入 messages 历史。
    # 这样 researcher agent（create_agent）invoke 时看到 history 里有"用户已批准的
    # 研究计划"，结合 SYSTEM_PROMPT 的硬约束，知道搜索/写综述必须按 plan 范围，
    # 不会自己另起炉灶用无关 query（比如把"无人机 MAC"联想成"GNN"）。
    plan_summary = json.dumps(plan, ensure_ascii=False, indent=2)
    return {
        "plan": plan,
        "plan_status": "approved",
        "plan_generated_at": state.get("plan_generated_at") or _now_iso(),
        "sub_questions": sub_q,
        "search_queries": search_q,
        "messages": [
            AIMessage(
                content=(
                    f"用户已批准以下研究计划：\n\n{plan_summary}\n\n"
                    f"请严格按上述计划的 title / outline / search_queries 范围"
                    f"进行搜索与综述撰写，不得偏离。"
                )
            )
        ],
    }