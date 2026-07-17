"""M3 研究模式：节点单测 + graph 端到端（全 mock，不烧 token/不联网）。"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PLANNER_JSON = json.dumps(
    {
        "sub_questions": ["子问题1", "子问题2"],
        "search_queries": ["llm reasoning", "chain of thought"],
    }
)

FAKE_PAPERS = [
    {
        "arxiv_id": "2401.00001",
        "title": "A Study on LLM Reasoning",
        "authors": ["Alice", "Bob"],
        "year": 2024,
        "abstract": "We study reasoning in large language models.",
        "pdf_url": "https://arxiv.org/pdf/2401.00001",
    },
    {
        "arxiv_id": "2312.00002",
        "title": "Chain of Thought Revisited",
        "authors": ["Carol"],
        "year": 2023,
        "abstract": "Revisiting CoT prompting.",
        "pdf_url": "https://arxiv.org/pdf/2312.00002",
    },
]

SYNTH = "综述正文……方法见 [2401.00001]，趋势见 [2312.00002]。"


def test_planner_parses_structured_output() -> None:
    import asyncio

    from app.agents.nodes import planner

    with patch("app.agents.nodes.planner.call_llm", new=AsyncMock(return_value=PLANNER_JSON)):
        out = asyncio.run(planner.run({"query": "LLM 推理优化"}))
    assert out["sub_questions"] == ["子问题1", "子问题2"]
    assert out["search_queries"] == ["llm reasoning", "chain of thought"]


def test_searcher_dedupes_across_queries() -> None:
    import asyncio

    from app.agents.nodes import searcher

    # 两个 query 都命中同一批（含重复 id）→ run() 按 arxiv_id 去重
    # 年份过滤在 _search_one 内（此处被 mock 掉），故不在本用例覆盖
    raw = FAKE_PAPERS + [{**FAKE_PAPERS[0]}]  # 重复 2401.00001
    with patch("app.agents.nodes.searcher._search_one", return_value=raw):
        out = asyncio.run(
            searcher.run(
                {"query": "x", "search_queries": ["q1", "q2"], "depth": "normal", "max_papers": 20}
            )
        )
    ids = [p["arxiv_id"] for p in out["papers"]]
    assert sorted(ids) == ["2312.00002", "2401.00001"]  # 去重后 2 篇


def test_research_endpoint_e2e() -> None:
    with (
        patch("app.agents.nodes.planner.call_llm", new=AsyncMock(return_value=PLANNER_JSON)),
        patch("app.agents.nodes.searcher._search_one", return_value=FAKE_PAPERS),
        patch("app.agents.nodes.synthesizer.call_llm", new=AsyncMock(return_value=SYNTH)),
    ):
        r = client.post("/api/research", json={"query": "LLM 推理优化", "depth": "quick"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report_markdown"] == SYNTH
    assert len(body["papers"]) == 2
    assert body["papers"][0]["arxiv_id"] == "2401.00001"
    assert body["report_path"].endswith(f"{body['session_id']}.md")
    # 落盘验证
    from pathlib import Path
    assert Path(body["report_path"]).read_text(encoding="utf-8") == SYNTH


def test_research_endpoint_no_papers_still_returns() -> None:
    with (
        patch("app.agents.nodes.planner.call_llm", new=AsyncMock(return_value=PLANNER_JSON)),
        patch("app.agents.nodes.searcher._search_one", return_value=[]),
        patch("app.agents.nodes.synthesizer.call_llm", new=AsyncMock(return_value="不该被调用")),
    ):
        r = client.post("/api/research", json={"query": "极冷门方向", "depth": "quick"})
    assert r.status_code == 200
    assert r.json()["papers"] == []
    assert "未检索到" in r.json()["report_markdown"]


def test_research_stream_emits_steps_and_final() -> None:
    with (
        patch("app.agents.nodes.planner.call_llm", new=AsyncMock(return_value=PLANNER_JSON)),
        patch("app.agents.nodes.searcher._search_one", return_value=FAKE_PAPERS),
        patch("app.agents.nodes.synthesizer.call_llm", new=AsyncMock(return_value=SYNTH)),
    ):
        r = client.post("/api/research/stream", json={"query": "LLM 推理优化", "depth": "quick"})
    assert r.status_code == 200
    text = r.text
    # 三个节点各一条 step
    for node in ("planner", "searcher", "synthesizer"):
        assert f'"node": "{node}"' in text
    assert "event: final" in text
    assert SYNTH in text

