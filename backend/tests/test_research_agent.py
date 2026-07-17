"""M4.5 研究 ReAct agent 测试：脚本化 fake model 驱动，不烧 token/不联网。"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.agents.context import ResearchContext
from app.agents.tools import make_tools
from app.main import app

client = TestClient(app)

FAKE_PAPERS = [
    {
        "arxiv_id": "2401.00001",
        "title": "A Study on LLM Reasoning",
        "authors": ["Alice"],
        "year": 2024,
        "abstract": "We study reasoning in LLMs.",
        "pdf_url": "https://arxiv.org/pdf/2401.00001",
    },
    {
        "arxiv_id": "2312.00002",
        "title": "CoT Revisited",
        "authors": ["Bob"],
        "year": 2023,
        "abstract": "Revisiting CoT.",
        "pdf_url": "https://arxiv.org/pdf/2312.00002",
    },
]

SYNTH = "综述正文……[2401.00001][2312.00002]。"
REVIEW_JSON = json.dumps(
    {"covered_subquestions": ["q"], "citation_accuracy": "ok", "issues": [], "passed": True}
)


class ScriptedModel(FakeMessagesListChatModel):
    """按脚本吐 AIMessage（含 tool_calls）的假模型；bind_tools 直接返回自己。"""

    def bind_tools(self, tools, **kw):  # noqa: ANN001
        return self


def _tc(name: str, args: dict, i: str):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": i}])


def _full_run_script() -> list[AIMessage]:
    return [
        _tc("search_arxiv", {"queries": ["llm reasoning", "cot"]}, "1"),
        _tc("download_papers", {}, "2"),
        _tc("analyze_papers", {}, "3"),
        _tc("write_review", {}, "4"),
        _tc("review_report", {}, "5"),
        AIMessage(content="综述已完成。"),
    ]


async def _fail_download(client, sem, paper, dest_dir):  # noqa: ANN001
    return paper, False


def _patches(script: list[AIMessage]):
    return (
        patch("app.agents.research_agent.get_llm", return_value=ScriptedModel(responses=script)),
        patch("app.agents.nodes.searcher._search_one", return_value=FAKE_PAPERS),
        patch("app.agents.nodes.downloader._download_one", new=_fail_download),
        patch("app.agents.nodes.synthesizer.call_llm", new=AsyncMock(return_value=SYNTH)),
        patch("app.agents.nodes.reviewer.call_llm", new=AsyncMock(return_value=REVIEW_JSON)),
    )


def test_agent_full_run_produces_report() -> None:
    p1, p2, p3, p4, p5 = _patches(_full_run_script())
    with p1, p2, p3, p4, p5:
        r = client.post("/api/research", json={"query": "LLM 推理优化", "depth": "quick"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report_markdown"] == SYNTH
    assert len(body["papers"]) == 2
    from pathlib import Path

    assert Path(body["report_path"]).read_text(encoding="utf-8") == SYNTH


def test_two_researches_same_session_do_not_overwrite_report() -> None:
    """同一 session 跑两次研究，report 文件不互相覆盖（修问题2）。"""
    from pathlib import Path

    p1, p2, p3, p4, p5 = _patches(_full_run_script())
    with p1, p2, p3, p4, p5:
        r1 = client.post("/api/research", json={"query": "方向A", "depth": "quick"})
    sid = r1.json()["session_id"]
    path1 = r1.json()["report_path"]

    q1, q2, q3, q4, q5 = _patches(_full_run_script())
    with q1, q2, q3, q4, q5:
        r2 = client.post("/api/research", json={"query": "方向B", "session_id": sid})
    path2 = r2.json()["report_path"]

    assert path1 != path2  # 不同文件
    assert Path(path1).read_text(encoding="utf-8") == SYNTH  # 第一份仍在


def test_agent_stream_emits_tool_steps_and_final() -> None:
    p1, p2, p3, p4, p5 = _patches(_full_run_script())
    with p1, p2, p3, p4, p5:
        r = client.post("/api/research/stream", json={"query": "LLM 推理优化", "depth": "quick"})
    assert r.status_code == 200
    text = r.text
    for tool_name in ("search_arxiv", "download_papers", "write_review", "review_report"):
        assert f'"node": "{tool_name}"' in text
    assert "event: final" in text
    assert SYNTH in text


# === tool 前置校验（半约束的核心，直接调 tool，不过 model）===

def test_tool_preconditions_block_out_of_order() -> None:
    ctx = ResearchContext(query="x")
    tools = {t.name: t for t in make_tools(ctx)}
    # 还没检索就下载 / 分析 / 综述 / 审校 → 各自报错提示
    assert "错误" in asyncio.run(tools["download_papers"].ainvoke({}))
    assert "错误" in asyncio.run(tools["analyze_papers"].ainvoke({}))
    assert "错误" in asyncio.run(tools["write_review"].ainvoke({}))
    assert "错误" in asyncio.run(tools["review_report"].ainvoke({}))


def test_search_tool_accumulates_and_dedupes() -> None:
    ctx = ResearchContext(query="x", depth="normal", max_papers=20)
    tools = {t.name: t for t in make_tools(ctx)}
    with patch("app.agents.nodes.searcher._search_one", return_value=FAKE_PAPERS + [FAKE_PAPERS[0]]):
        asyncio.run(tools["search_arxiv"].ainvoke({"queries": ["q1"]}))
        asyncio.run(tools["search_arxiv"].ainvoke({"queries": ["q2"]}))  # 再搜，去重
    assert sorted(p["arxiv_id"] for p in ctx.papers) == ["2312.00002", "2401.00001"]
