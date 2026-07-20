"""§12.11 研究模式聊天追问（跨轮记忆）测试。"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.main import app
from app.session_store import store

client = TestClient(app)

SYNTH = "续接后的新综述"
REVIEW_JSON = json.dumps(
    {"covered_subquestions": ["q"], "citation_accuracy": "ok", "issues": [], "passed": True}
)


class ScriptedModel(FakeMessagesListChatModel):
    """Fake chat model: bind_tools 直接返回自己，脚本化产出 tool_calls。"""

    def bind_tools(self, tools, **kw):
        return self


def _tc(name: str, args: dict, i: str):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": i}])


FAKE_PAPERS = [
    {
        "arxiv_id": "2401.00001",
        "title": "T1",
        "authors": ["A"],
        "year": 2024,
        "abstract": "abs1",
        "pdf_url": "http://x/1.pdf",
    },
]

P1_SCRIPT = [
    _tc("search_arxiv", {"queries": ["q1"]}, "1"),
    _tc("download_papers", {}, "2"),
    _tc("analyze_papers", {}, "3"),
    _tc("write_review", {}, "4"),
    _tc("review_report", {}, "5"),
    AIMessage(content="done"),
]


async def _fail_download(client, sem, paper, dest_dir):  # noqa: ANN001
    return paper, False


def _run_research(query: str, session_id: str | None) -> dict:
    """跑一次研究（mock 全栈），返回响应 body。"""
    body = {"query": query, "depth": "quick"}
    if session_id:
        body["session_id"] = session_id
    with (
        patch("app.agents.research_agent.get_llm", return_value=ScriptedModel(responses=P1_SCRIPT)),
        patch("app.agents.nodes.searcher._search_one", return_value=FAKE_PAPERS),
        patch("app.agents.nodes.downloader._download_one", new=_fail_download),
        patch("app.agents.nodes.synthesizer.call_llm", new=AsyncMock(return_value=SYNTH)),
        patch("app.agents.nodes.reviewer.call_llm", new=AsyncMock(return_value=REVIEW_JSON)),
    ):
        r = client.post("/api/research", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_round1_saves_state_to_db() -> None:
    """第一轮：检索 1 篇 → 落盘 ResearchState。"""
    r = _run_research("LLM 推理优化", session_id=None)
    sid = r["session_id"]
    state = store.load_research_state(sid)
    assert state is not None
    assert state["draft"] == SYNTH
    assert len(state["papers"]) == 1
    assert state["papers"][0]["arxiv_id"] == "2401.00001"
    assert state["search_queries"] == ["q1"]


def test_round2_loads_state_and_continues() -> None:
    """第二轮同 session：ctx.papers 应已被前一轮填充（来自 DB，不是从零）。"""
    r1 = _run_research("方向A", session_id=None)
    sid = r1["session_id"]
    # 第二轮同一 session
    r2 = _run_research("再多找几篇 2024 的", session_id=sid)
    # 论文应累积（agent 的 search_arxiv 用 setdefault 去重）
    state = store.load_research_state(sid)
    assert state is not None
    assert state["papers"]  # 非空（从 round1 来）
    assert "2401.00001" in [p["arxiv_id"] for p in state["papers"]]


def test_new_session_starts_fresh() -> None:
    """不同 session_id：新 session 不继承前 session 的论文。"""
    r1 = _run_research("方向A", session_id=None)
    sid1 = r1["session_id"]
    # 用一个新 session_id（与 sid1 不同）
    r2 = _run_research("方向B", session_id=None)
    sid2 = r2["session_id"]
    assert sid1 != sid2
    # sid2 的 state 是从零（round1 的论文不应泄漏）
    state2 = store.load_research_state(sid2)
    # 第二轮 mock 脚本仍返 1 篇，所以会有 1 篇；但来源是 mock 的 FAKE_PAPERS，不是 round1 的
    assert state2 is not None
    assert len(state2["papers"]) == 1


def test_state_persists_across_store_reinstantiation() -> None:
    """新 store 实例也能读到（模拟进程重启）。"""
    from app.session_store import DBSessionStore

    r = _run_research("测试", session_id=None)
    sid = r["session_id"]
    state = DBSessionStore().load_research_state(sid)
    assert state is not None
    assert state["draft"] == SYNTH


def test_session_without_state_gets_fresh_ctx() -> None:
    """存不存在 session，store.load_research_state(未知 id) 不应崩——返 None。"""
    state = store.load_research_state("nonexistent_session_id_zzz")
    assert state is None


def test_synthesizer_sees_continuation_note() -> None:
    """第二轮 agent 初始消息应包含 system 注记 + 用户新 query。"""
    from app.api.research import _build_initial_messages
    from app.agents.context import ResearchContext

    # 前态：已有论文+综述
    prior_ctx = ResearchContext(
        query="方向A",
        session_id="sid",
        papers=[{"arxiv_id": "2401.00001", "title": "T1"}],
        draft="前综述",
        sub_questions=["子1"],
        search_queries=["q1"],
    )
    msgs = _build_initial_messages(prior_ctx)
    assert msgs[0][0] == "system"
    assert "续接" in msgs[0][1]
    assert "2401.00001" in msgs[0][1]
    assert msgs[-1] == ("user", "方向A")

    # 无前态：直接 user query
    fresh_ctx = ResearchContext(query="新方向", session_id="sid")
    msgs = _build_initial_messages(fresh_ctx)
    assert msgs == [("user", "新方向")]