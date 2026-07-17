"""M4.2 Analyzer 节点测试（mock PDF + LLM）。"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from app.agents.nodes import analyzer
from app.rag.pdf_loader import PageText

ANALYSIS_JSON = json.dumps(
    {"problem": "p", "method": "m", "results": "r", "limitations": "l", "novelty": "n"}
)


def test_analyze_paper_with_local_path() -> None:
    papers = [{"arxiv_id": "2401.00001", "title": "T", "local_path": "/x/1.pdf"}]
    with (
        patch("app.agents.nodes.analyzer.load_pdf", return_value=[PageText(1, "some paper text")]),
        patch("app.agents.nodes.analyzer.call_llm", new=AsyncMock(return_value=ANALYSIS_JSON)),
    ):
        out = asyncio.run(analyzer.run({"papers": papers}))
    assert len(out["analyses"]) == 1
    a = out["analyses"][0]
    assert a["arxiv_id"] == "2401.00001"
    assert a["method"] == "m" and a["novelty"] == "n"


def test_analyze_skips_without_local_path() -> None:
    out = asyncio.run(analyzer.run({"papers": [{"arxiv_id": "x", "title": "T"}]}))
    assert out["analyses"] == []


def test_analyze_skips_empty_text() -> None:
    papers = [{"arxiv_id": "y", "title": "T", "local_path": "/x/y.pdf"}]
    with patch("app.agents.nodes.analyzer.load_pdf", return_value=[PageText(1, "   ")]):
        out = asyncio.run(analyzer.run({"papers": papers}))
    assert out["analyses"] == []  # 扫描件/空文本跳过
