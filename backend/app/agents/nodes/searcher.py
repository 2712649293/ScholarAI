"""Searcher 节点：arxiv 检索 + 去重 + 年份过滤 + 限速（M3.2）。"""
from __future__ import annotations

import asyncio

import arxiv

from app.agents.state import ResearchState
from app.errors import ArxivFetchFailed

YEAR_MIN = 2018
PER_QUERY = {"quick": 5, "normal": 10, "deep": 15}


def _search_one(query: str, max_results: int) -> list[dict]:
    """同步调 arxiv（SDK 内置 delay_seconds 限速），返回论文 dict 列表。"""
    client = arxiv.Client(page_size=max_results, delay_seconds=3, num_retries=2)
    search = arxiv.Search(
        query=query, max_results=max_results, sort_by=arxiv.SortCriterion.Relevance
    )
    out: list[dict] = []
    for r in client.results(search):
        year = r.published.year if r.published else 0
        if year < YEAR_MIN:
            continue
        out.append(
            {
                "arxiv_id": r.get_short_id(),
                "title": r.title.strip(),
                "authors": [a.name for a in r.authors],
                "year": year,
                "abstract": r.summary.strip(),
                "pdf_url": r.pdf_url,
            }
        )
    return out


async def run(state: ResearchState) -> ResearchState:
    queries = state.get("search_queries") or [state["query"]]
    per = PER_QUERY.get(state.get("depth", "normal"), 10)
    max_papers = state.get("max_papers", 20)

    merged: dict[str, dict] = {}
    errors = 0
    for q in queries:
        try:
            hits = await asyncio.to_thread(_search_one, q, per)
        except Exception:  # 单 query 失败不阻断（§8.3 降级）
            errors += 1
            continue
        for h in hits:
            merged.setdefault(h["arxiv_id"], h)

    if not merged and errors:  # 整轮全失败才抛错
        raise ArxivFetchFailed("arxiv 检索全部失败", details={"queries": queries})

    papers = sorted(merged.values(), key=lambda p: p["year"], reverse=True)[:max_papers]
    return {"papers": papers}
