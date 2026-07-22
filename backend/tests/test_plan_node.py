"""M5.5.1 · Planner 节点单测。

- generate_plan: LLM 输出解析 / fallback 路径
- planner_node: 首次 interrupt / resume approve / resume reject / edited_plan 路径

参考 test_analyzer.py / test_chat.py 的 patch 套路。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.nodes.planner import generate_plan, planner_node
from app.agents.schemas import ResearchPlan

# 标准 mock plan 输出（with_structured_output 路径）
MOCK_PLAN = ResearchPlan(
    title="LLM 推理综述",
    sub_questions=[
        {"question": "q1", "rationale": "r1"},
        {"question": "q2", "rationale": "r2"},
        {"question": "q3", "rationale": "r3"},
    ],
    search_queries=[
        {"intent": "i1", "queries": ["a", "b"]},
        {"intent": "i2", "queries": ["c", "d"]},
        {"intent": "i3", "queries": ["e", "f"]},
    ],
    outline=[
        {"heading": "h1", "bullets": ["b1"]},
        {"heading": "h2", "bullets": ["b2"]},
        {"heading": "h3", "bullets": ["b3"]},
        {"heading": "h4", "bullets": ["b4"]},
        {"heading": "h5", "bullets": ["b5"]},
    ],
    estimated_papers=20,
    reasoning="test reasoning",
)


def test_generate_plan_returns_research_plan():
    """with_structured_output 路径直接返 ResearchPlan 对象。"""
    struct_mock = AsyncMock()
    struct_mock.ainvoke = AsyncMock(return_value=MOCK_PLAN)
    fake_llm = AsyncMock()
    fake_llm.with_structured_output = lambda _schema: struct_mock
    with patch("app.agents.nodes.planner.get_llm", return_value=fake_llm):
        result = asyncio.run(generate_plan("LLM 推理", "normal"))
    assert isinstance(result, ResearchPlan)
    assert result.title == "LLM 推理综述"
    assert len(result.sub_questions) == 3


def test_generate_plan_handles_dict_return():
    """with_structured_output 返 dict 时走 model_validate。"""
    struct_mock = AsyncMock()
    struct_mock.ainvoke = AsyncMock(return_value=MOCK_PLAN.model_dump())
    fake_llm = AsyncMock()
    fake_llm.with_structured_output = lambda _schema: struct_mock
    with patch("app.agents.nodes.planner.get_llm", return_value=fake_llm):
        result = asyncio.run(generate_plan("LLM 推理"))
    assert isinstance(result, ResearchPlan)


def test_generate_plan_fallback_to_json_mode():
    """with_structured_output 失败 → fallback 到 JSON mode + Pydantic。"""
    fake_llm = AsyncMock()
    # with_structured_output 抛错
    fake_llm.with_structured_output.side_effect = RuntimeError("not supported")
    # bind(response_format=...) 返回的 mock 有 ainvoke
    fake_bound = AsyncMock()
    fake_bound.ainvoke = AsyncMock(return_value=AsyncMock(content=MOCK_PLAN.model_dump_json()))
    fake_llm.bind = lambda **kw: fake_bound

    with patch("app.agents.nodes.planner.get_llm", return_value=fake_llm):
        result = asyncio.run(generate_plan("LLM 推理"))
    assert isinstance(result, ResearchPlan)
    assert result.title == "LLM 推理综述"


def test_planner_node_first_call_invokes_interrupt():
    """首次进入：生成 plan + 调 interrupt(payload) 抛异常（模拟 graph 内的 GraphInterrupt）。

    ponytail：在 graph runtime 内 interrupt() 抛 GraphInterrupt，单测里没有 graph
    上下文，直接调会报 'outside of a runnable context'。改成 mock interrupt 让它
    抛 RuntimeError，验证 planner_node 至少调过 interrupt 一次 + 拿到了 plan payload。
    """
    struct_mock = AsyncMock()
    struct_mock.ainvoke = AsyncMock(return_value=MOCK_PLAN)
    fake_llm = AsyncMock()
    fake_llm.with_structured_output = lambda _schema: struct_mock

    interrupt_payloads: list = []

    def fake_interrupt(payload):
        interrupt_payloads.append(payload)
        raise RuntimeError("simulating GraphInterrupt")

    with patch("app.agents.nodes.planner.get_llm", return_value=fake_llm), \
         patch("app.agents.nodes.planner.interrupt", side_effect=fake_interrupt):
        with pytest.raises(RuntimeError, match="simulating"):
            asyncio.run(
                planner_node({"query": "LLM 推理", "session_id": "sess-1", "depth": "normal"})
            )

    # interrupt 被调一次，payload 含 plan dict + session_id
    assert len(interrupt_payloads) == 1
    assert interrupt_payloads[0]["plan"] == MOCK_PLAN.model_dump()
    assert interrupt_payloads[0]["session_id"] == "sess-1"


def test_planner_node_resume_reject_returns_rejected():
    """resume + action=reject → plan_status='rejected'。"""
    state = {
        "query": "LLM 推理",
        "session_id": "s1",
        "plan": MOCK_PLAN.model_dump(),
        "plan_status": "pending",
    }
    with patch(
        "app.agents.nodes.planner.interrupt",
        return_value={"action": "reject"},
    ):
        result = asyncio.run(planner_node(state))
    assert result["plan_status"] == "rejected"
    # 没生成 sub_questions / search_queries（不传给 researcher）
    assert "sub_questions" not in result


def test_planner_node_resume_approve_uses_edited_plan():
    """resume + action=approve + edited_plan → 用编辑过的 plan，覆盖 sub_questions/search_queries。"""
    state = {
        "query": "LLM 推理",
        "session_id": "s1",
        "plan": MOCK_PLAN.model_dump(),
        "plan_status": "pending",
    }
    edited = MOCK_PLAN.model_dump()
    edited["title"] = "编辑过的标题"
    with patch(
        "app.agents.nodes.planner.interrupt",
        return_value={"action": "approve", "edited_plan": edited},
    ):
        result = asyncio.run(planner_node(state))
    assert result["plan_status"] == "approved"
    assert result["plan"]["title"] == "编辑过的标题"
    # sub_questions 是字符串列表（researcher 用），不是 dict 列表
    assert isinstance(result["sub_questions"], list)
    assert all(isinstance(s, str) for s in result["sub_questions"])
    # search_queries 是所有 queries 拍平后的字符串列表
    assert isinstance(result["search_queries"], list)
    assert all(isinstance(q, str) for q in result["search_queries"])


def test_planner_node_resume_approve_uses_original_plan():
    """resume + action=approve 不带 edited_plan → 用 state 现有 plan。"""
    state = {
        "query": "LLM 推理",
        "session_id": "s1",
        "plan": MOCK_PLAN.model_dump(),
        "plan_status": "pending",
    }
    with patch(
        "app.agents.nodes.planner.interrupt",
        return_value={"action": "approve"},
    ):
        result = asyncio.run(planner_node(state))
    assert result["plan_status"] == "approved"
    assert result["plan"] == MOCK_PLAN.model_dump()


def test_planner_node_resume_invalid_decision_treated_as_reject():
    """resume 拿到非法决议（非 dict）→ 当 reject 处理（容错）。"""
    state = {
        "query": "LLM 推理",
        "session_id": "s1",
        "plan": MOCK_PLAN.model_dump(),
        "plan_status": "pending",
    }
    with patch("app.agents.nodes.planner.interrupt", return_value="garbage"):
        result = asyncio.run(planner_node(state))
    assert result["plan_status"] == "rejected"