"""M4.2 Analyzer 节点测试（mock PDF + LLM）。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
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


def test_analyze_markdown_by_heading_and_persists_artifact(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"placeholder")
    section_output = json.dumps(
        {
            "summary": "研究方法",
            "key_points": ["关键点"],
            "methods": ["方法"],
            "results": ["结果"],
            "limitations": [],
            "evidence": [],
        }
    )
    paper_output = json.dumps(
        {
            "problem": "问题",
            "method": "方法",
            "results": "结果",
            "limitations": "局限",
            "novelty": "新意",
        }
    )

    def fake_load_pdf(path: str, output_dir: Path | None = None) -> list[PageText]:
        assert output_dir is not None
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "paper.enriched.md").write_text(
            "# 方法\n<!-- page:1 -->\n方法内容\n\n# 实验\n<!-- page:2 -->\n实验内容",
            encoding="utf-8",
        )
        return [PageText(1, "方法内容"), PageText(2, "实验内容")]

    with (
        patch("app.agents.nodes.analyzer.load_pdf", side_effect=fake_load_pdf),
        patch(
            "app.agents.nodes.analyzer.call_llm",
            new=AsyncMock(side_effect=[section_output, section_output, paper_output]),
        ) as mocked_llm,
    ):
        out = asyncio.run(
            analyzer.run(
                {
                    "papers": [
                        {
                            "arxiv_id": "2401.00003",
                            "title": "T",
                            "local_path": str(pdf_path),
                        }
                    ]
                }
            )
        )

    analysis = out["analyses"][0]
    assert mocked_llm.await_count == 3
    assert all(call.kwargs["use_small_model"] for call in mocked_llm.await_args_list)
    assert [item["heading"] for item in analysis["section_analyses"]] == ["方法", "实验"]
    artifact_dir = pdf_path.parent / "arxiv_2401.00003.analysis"
    assert Path(analysis["analysis_path"]).is_file()
    assert json.loads((artifact_dir / "analysis.json").read_text(encoding="utf-8"))["novelty"] == "新意"
    assert (artifact_dir / "paper.enriched.md").is_file()


def test_analyze_only_selected_papers(tmp_path: Path) -> None:
    paths = [tmp_path / "one.pdf", tmp_path / "two.pdf"]
    for path in paths:
        path.write_bytes(b"placeholder")

    def fake_load_pdf(path: str, output_dir: Path | None = None) -> list[PageText]:
        assert output_dir is not None
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "paper.enriched.md").write_text("# 章节\n内容", encoding="utf-8")
        return [PageText(1, "内容")]

    section_output = json.dumps({"summary": "s", "methods": [], "results": [], "limitations": []})
    paper_output = json.dumps({"problem": "p", "method": "m", "results": "r", "limitations": "l", "novelty": "n"})
    with (
        patch("app.agents.nodes.analyzer.load_pdf", side_effect=fake_load_pdf),
        patch("app.agents.nodes.analyzer.call_llm", new=AsyncMock(side_effect=[section_output, paper_output])),
    ):
        out = asyncio.run(
            analyzer.run(
                {
                    "papers": [
                        {"arxiv_id": "one", "title": "One", "local_path": str(paths[0])},
                        {"arxiv_id": "two", "title": "Two", "local_path": str(paths[1])},
                    ],
                    "selected_paper_keys": ["arxiv:one"],
                }
            )
        )

    assert [item["arxiv_id"] for item in out["analyses"]] == ["one"]


def test_section_paths_include_empty_parents_and_reset_at_siblings() -> None:
    sections = analyzer.split_sections(
        "# Paper\n## Methods\n### Setup\nFirst setup\n"
        "## Results\n### Setup\nSecond setup\n"
    )
    assert [section["heading_path"] for section in sections] == [
        "Paper", "Paper > Methods", "Paper > Methods > Setup",
        "Paper > Results", "Paper > Results > Setup",
    ]
    assert [section["text"] for section in analyzer._bounded_sections(sections)] == [
        "First setup", "Second setup",
    ]


def test_abstract_prefers_document_then_metadata_without_generation() -> None:
    paper = {"abstract": "Search abstract"}
    for heading in ("Abstract", "ABSTRACT", "摘要", "1. Abstract"):
        sections = analyzer.split_sections(f"## {heading}\n<!-- page:1 -->\nOriginal abstract\n## Method\nBody")
        assert analyzer._paper_abstract(paper, sections) == "Original abstract"
    sections = analyzer.split_sections("Body without headings")
    assert analyzer._paper_abstract(paper, sections) == "Search abstract"
    assert analyzer._paper_abstract({"abstract": None}, sections) == ""
    assert sections[0]["heading_path"] == "全文"


def test_each_fragment_receives_original_abstract_and_full_index(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"placeholder")
    markdown = (
        "# Paper\n## Abstract\nOriginal abstract.\n"
        "## Methods\n### Setup\nabcdefghijklmnop\n## Results\nResult."
    )
    section_output = json.dumps({"summary": "s", "methods": ["m"]})
    paper = {"title": "Paper", "arxiv_id": "context", "abstract": "Search abstract", "local_path": str(pdf_path)}

    async def fake_load_document(path: str, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        return markdown, []

    async def fake_call(messages, **kwargs):
        assert kwargs["use_small_model"] is True
        return section_output if "[当前章节原文]" in messages[1].content else ANALYSIS_JSON

    with (
        patch.object(analyzer, "_load_document", new=AsyncMock(side_effect=fake_load_document)),
        patch.object(analyzer, "MAX_SECTION_CHARS", 10),
        patch.object(analyzer, "call_llm", new=AsyncMock(side_effect=fake_call)) as mocked,
    ):
        out = asyncio.run(analyzer.run({"papers": [paper]}))

    prompts = [call.args[0][1].content for call in mocked.await_args_list[:-1]]
    for prompt in prompts:
        assert "[论文摘要]\nOriginal abstract.\n" in prompt
        assert "Search abstract" not in prompt
        assert "[章节结构索引（全部标题，一行）]\nPaper | Paper > Abstract | Paper > Methods | Paper > Methods > Setup | Paper > Results\n" in prompt
        fields = ["[论文标题]", "[论文摘要]", "[章节结构索引（全部标题，一行）]", "[当前章节标题路径]", "[当前章节原文]"]
        assert [prompt.index(field) for field in fields] == sorted(prompt.index(field) for field in fields)
    setup_prompts = [prompt for prompt in prompts if "[当前章节标题路径]\nPaper > Methods > Setup\n" in prompt]
    assert len(setup_prompts) == 2
    assert [prompt.split("[当前章节原文]\n", 1)[1] for prompt in setup_prompts] == ["abcdefghij", "klmnop"]
    analysis = out["analyses"][0]
    assert analysis["status"] == "completed"
    assert analysis["analysis_version"] == "paper-analysis-v3"
    assert analysis["method"] == "m"
    assert len(analysis["section_analyses"]) == 5


def test_small_model_failure_does_not_retry_with_main_model(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"placeholder")
    with (
        patch.object(analyzer, "_load_document", new=AsyncMock(return_value=("# Method\nBody", []))),
        patch.object(analyzer, "call_llm", new=AsyncMock(side_effect=RuntimeError("small model unavailable"))) as mocked,
    ):
        out = asyncio.run(analyzer.run({"papers": [{"title": "Paper", "local_path": str(pdf_path)}]}))
    assert mocked.await_count == 1
    assert mocked.await_args.kwargs["use_small_model"] is True
    assert out["analyses"][0]["status"] == "failed"
    assert "未找到论文摘要原文" in out["analyses"][0]["parser_warnings"][0]
