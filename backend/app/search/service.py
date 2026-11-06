"""Paper discovery through the external ``paper-search-mcp`` MCP server."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any

from app.search.mcp_client import PaperSearchMCP, PaperSearchMCPError
from app.search.models import (
    PaperRecord,
    SearchResult,
    merge_paper,
    normalize_doi,
    paper_identity_keys,
)


def _year(value: Any) -> int:
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return int(match.group(0)) if match else 0


def _authors(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(";") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _paper_record(item: Mapping[str, Any]) -> PaperRecord:
    source = str(item.get("source") or "paper-search-mcp")
    paper_id = str(item.get("paper_id") or item.get("arxiv_id") or item.get("doi") or "")
    doi = normalize_doi(str(item.get("doi") or "") or None)
    arxiv_id = item.get("arxiv_id")
    if not arxiv_id and source == "arxiv" and paper_id:
        arxiv_id = paper_id
    record: PaperRecord = {
        "paper_key": "",
        "title": str(item.get("title") or "").strip(),
        "authors": _authors(item.get("authors")),
        "year": _year(item.get("published_date") or item.get("year")),
        "abstract": str(item.get("abstract") or "").strip(),
        "doi": doi,
        "arxiv_id": str(arxiv_id) if arxiv_id else None,
        "source_ids": {source: paper_id} if paper_id else {},
        "sources": [source],
        "pdf_url": item.get("pdf_url") or None,
        "landing_url": item.get("url") or None,
        "is_open_access": bool(item.get("pdf_url")),
        "provider_score": float(item.get("citations") or 0),
    }
    record["paper_key"] = paper_identity_keys(record)[0]
    return record


class SearchAgent:
    """Call MCP once per query; source selection belongs to the MCP server."""

    def __init__(self, *, max_iterations: int = 1, per_source_limit: int = 20, concurrency: int = 1) -> None:
        self.max_iterations = max_iterations
        self.per_source_limit = per_source_limit
        self.concurrency = concurrency

    @staticmethod
    def _expand_queries(state: Mapping[str, Any]) -> list[str]:
        queries = state.get("search_queries") or []
        if queries and isinstance(queries[0], dict):
            queries = [q for group in queries for q in group.get("queries", [])]
        if queries:
            return list(dict.fromkeys(str(q).strip() for q in queries if str(q).strip()))[:5]
        direction = state.get("clarify_direction") or {}
        refined = direction.get("refined_query") if isinstance(direction, Mapping) else None
        query = str(refined or state.get("query", "")).strip()
        return [query] if query else []

    async def run(self, state: dict[str, Any]) -> SearchResult:
        queries = self._expand_queries(state)
        year_info = state.get("clarify_direction") or {}
        year_start = year_info.get("year_start") if isinstance(year_info, Mapping) else None
        year_end = year_info.get("year_end") if isinstance(year_info, Mapping) else None
        year = ""
        if year_start or year_end:
            year = str(year_start or "")
            if year_end:
                year = f"{year}-{year_end}" if year else str(year_end)
        errors: dict[str, str] = {}
        merged: dict[str, PaperRecord] = {}
        aliases: dict[str, str] = {}
        sources: set[str] = set()
        client = PaperSearchMCP()
        results = await asyncio.gather(
            *(
                client.search(
                    query, max_results_per_source=self.per_source_limit, year=year
                )
                for query in queries
            ),
            return_exceptions=True,
        )
        for payload in results:
            if isinstance(payload, PaperSearchMCPError):
                errors["paper-search-mcp"] = str(payload)
                continue
            if not isinstance(payload, Mapping):
                errors["paper-search-mcp"] = "invalid MCP search result"
                continue
            for source in payload.get("sources_used", []) or []:
                sources.add(str(source))
            for source, message in (payload.get("errors") or {}).items():
                errors[str(source)] = str(message)
            for item in payload.get("papers", []) or []:
                if not isinstance(item, Mapping):
                    continue
                paper = _paper_record(item)
                keys = paper_identity_keys(paper)
                key = next((aliases[alias] for alias in keys if alias in aliases), keys[0])
                paper["paper_key"] = key
                merged[key] = merge_paper(merged[key], paper) if key in merged else paper
                for alias in paper_identity_keys(merged[key]):
                    aliases[alias] = key

        papers = list(merged.values())
        papers.sort(key=lambda paper: (bool(paper.get("abstract")), paper.get("year", 0)), reverse=True)
        cap = int(state.get("max_papers", 20))
        if papers and not errors:
            status = "completed"
        elif papers:
            status = "partial"
        elif errors:
            status = "failed"
        else:
            status = "empty"
        return SearchResult(
            papers=papers[:cap],
            queries=queries,
            sources=sorted(sources) or ["paper-search-mcp"],
            source_errors=errors,
            iterations=1 if queries else 0,
            status=status,
        )


def result_to_state(result: SearchResult) -> dict[str, Any]:
    return {"papers": result.papers}
