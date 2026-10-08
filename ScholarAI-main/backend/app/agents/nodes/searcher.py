"""Searcher 节点：arxiv 检索 + 去重 + 年份过滤 + 限速 + retry（M3.2 + M5.5.9 + M5.5.10）。

- M5.5.9: 单 query timeout=30s，避免 SDK hang 卡死流
- M5.5.10: 单 query retry 3 次（1+2 retry），指数退避 2s/4s
- 保持串行——arxiv 对单 IP 限频，并发会被全 ban
"""
from __future__ import annotations

import asyncio

import arxiv
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.agents.state import ResearchState
from app.errors import ArxivFetchFailed

YEAR_MIN = 2018
PER_QUERY = {"quick": 5, "normal": 10, "deep": 15}
# M5.5.9: 单 query 最多 30s——arxiv SDK 无内置 timeout，hang 时会卡死整个流。
QUERY_TIMEOUT = 30.0
# M5.5.10: 单 query retry 次数（不含首次）= 2 → 总尝试 3 次。
SEARCH_MAX_RETRIES = 2


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


@retry(
    stop=stop_after_attempt(SEARCH_MAX_RETRIES + 1),
    wait=wait_exponential(multiplier=1, min=2, max=8),  # 2s/4s/8s
    retry=retry_if_exception_type(Exception),  # 包括 TimeoutError
    reraise=True,
)
async def _search_with_timeout(q: str, per: int) -> list[dict]:
    """M5.5.9: 单 query 加 timeout。线程无法被外部 kill（asyncio.to_thread 限制），
    但 asyncio.wait_for 30s 后会立即返回上层，线程继续跑但下次 SDK 请求仍 OK。

    M5.5.10: tenacity retry 装饰——timeout/异常自动重试 2 次（指数退避 2s/4s）。
    """
    return await asyncio.wait_for(
        asyncio.to_thread(_search_one, q, per),
        timeout=QUERY_TIMEOUT,
    )


async def run(state: ResearchState) -> ResearchState:
    queries = state.get("search_queries") or [state["query"]]
    per = PER_QUERY.get(state.get("depth", "normal"), 10)
    max_papers = state.get("max_papers", 20)

    merged: dict[str, dict] = {}
    errors = 0
    for q in queries:
        try:
            hits = await _search_with_timeout(q, per)
        except Exception:  # 单 query 失败不阻断（§8.3 降级，含 timeout + retry exhausted）
            errors += 1
            continue
        for h in hits:
            merged.setdefault(h["arxiv_id"], h)

    if not merged and errors:  # 整轮全失败才抛错
        raise ArxivFetchFailed(
            "arxiv 检索全部失败",
            details={
                "queries": queries,
                "reason": "all_timeout_or_error",
                "attempts_per_query": SEARCH_MAX_RETRIES + 1,
            },
        )

    papers = sorted(merged.values(), key=lambda p: p["year"], reverse=True)[:max_papers]
    return {"papers": papers}
