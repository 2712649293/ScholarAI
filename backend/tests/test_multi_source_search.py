from __future__ import annotations

from unittest.mock import patch

import pytest

from app.search.mcp_client import PaperSearchMCPError
from app.search.service import SearchAgent, _paper_record, result_to_state


class FakeMCP:
    calls: list[tuple[str, str]] = []

    async def search(self, query: str, *, max_results_per_source: int, year: str = ""):
        self.calls.append((query, year))
        return {
            "sources_used": ["arxiv", "crossref"],
            "errors": {},
            "papers": [
                {
                    "paper_id": "2401.12345v1",
                    "title": "Shared paper",
                    "authors": "A; B",
                    "abstract": query,
                    "published_date": "2024-01-01",
                    "doi": "10.1000/shared",
                    "pdf_url": "https://example.test/paper.pdf",
                    "url": "https://example.test/paper",
                    "source": "arxiv",
                }
            ],
        }


@pytest.mark.asyncio
async def test_mcp_search_maps_and_merges_results_without_domain_routing():
    fake = FakeMCP()
    with patch("app.search.service.PaperSearchMCP", return_value=fake):
        result = await SearchAgent(per_source_limit=3).run(
            {
                "domain": "medicine",
                "search_queries": ["q1", "q2"],
                "max_papers": 10,
                "clarify_direction": {"year_start": 2020, "year_end": 2024},
            }
        )
    assert result.status == "completed"
    assert len(result.papers) == 1
    assert result.papers[0]["paper_key"] == "doi:10.1000/shared"
    assert result.papers[0]["authors"] == ["A", "B"]
    assert fake.calls == [("q1", "2020-2024"), ("q2", "2020-2024")]
    assert result_to_state(result) == {"papers": result.papers}


@pytest.mark.asyncio
async def test_mcp_failure_is_reported_without_network_fallback():
    class FailedMCP:
        async def search(self, *args, **kwargs):
            raise PaperSearchMCPError("offline")

    with patch("app.search.service.PaperSearchMCP", return_value=FailedMCP()):
        result = await SearchAgent().run({"search_queries": ["q"], "max_papers": 5})
    assert result.status == "failed"
    assert "paper-search-mcp" in result.source_errors
    assert result.papers == []


def test_paper_record_uses_source_id_and_arxiv_versionless_key():
    paper = _paper_record(
        {
            "paper_id": "2401.00001v2",
            "title": "T",
            "authors": "A",
            "published_date": "2024-03-01",
            "source": "arxiv",
        }
    )
    assert paper["arxiv_id"] == "2401.00001v2"
    assert paper["paper_key"] == "arxiv:2401.00001"
    assert paper["year"] == 2024
