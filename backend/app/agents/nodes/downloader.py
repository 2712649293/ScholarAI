"""Downloader 节点：并发下载论文 PDF（M4.1 + M5.5.10：retry + jitter + 总超时调整）。

M5.5.10 调整：
- 单 PDF timeout 15s → 30s（容忍慢响应）
- 总超时 180s → 240s（容纳 retry 间隔）
- retry 仍 3 次，但加 multiplier=1 让指数退避更明显（1s/2s/4s/8s）
- 并发 5 不变（PDF 是 fastly CDN 较稳，但 arxiv search 端串行——见 searcher.py）
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.agents.state import ResearchState
from app.config import settings

MAX_CONCURRENT = 5
DOWNLOAD_TOTAL_TIMEOUT = 240  # 全部下载总超时（秒）；到点未完成的算失败，防 httpx 卡死
PDF_TIMEOUT = 30.0  # M5.5.10: 单 PDF timeout 从 15s → 30s（容忍慢响应）
PDF_MAX_RETRIES = 2  # 1 次 + 2 次重试 = 共 3 次尝试
_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)


@retry(
    stop=stop_after_attempt(PDF_MAX_RETRIES + 1),
    wait=wait_exponential(multiplier=1, min=2, max=8),  # 2s/4s/8s
    retry=retry_if_exception_type(_RETRYABLE),
    reraise=True,
)
async def _fetch(client: httpx.AsyncClient, url: str) -> bytes:
    r = await client.get(url, follow_redirects=True, timeout=PDF_TIMEOUT)
    r.raise_for_status()
    return r.content


async def _download_one(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, paper: dict, dest_dir: Path
) -> tuple[dict, bool]:
    url = paper.get("pdf_url")
    if not url:
        return paper, False
    path = dest_dir / f"{paper['arxiv_id'].replace('/', '_')}.pdf"
    if path.exists() and path.stat().st_size > 0:  # 断点：已下过就跳过
        return {**paper, "local_path": str(path)}, True
    async with sem:
        try:
            content = await _fetch(client, url)
        except Exception:  # 单篇失败不阻断（§8.3 降级）
            return paper, False
    if not content:
        return paper, False
    path.write_bytes(content)
    return {**paper, "local_path": str(path)}, True


async def run(state: ResearchState) -> ResearchState:
    papers = state.get("papers", [])
    if not papers:
        return {"papers": [], "download_failures": []}

    # M2: 每 session 独立 PDF 目录（删 session 时一起删）
    session_id = state.get("session_id")
    base_dir = Path(settings.paper_storage_dir)
    dest_dir = base_dir / session_id if session_id else base_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient() as client:
        # 兜底：gather 等所有并发完成，单条卡死会一直挂。加总超时，到点取已下完的
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(_download_one(client, sem, p, dest_dir) for p in papers),
                    return_exceptions=True,
                ),
                timeout=DOWNLOAD_TOTAL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            # 全部标记失败（gather 在超时后行为不可靠；保守起见用 None，后续判定失败）
            results = [None] * len(papers)  # type: ignore[list-item]

    updated: list[dict] = []
    failures: list[str] = []
    for paper, r in zip(papers, results):
        if r is None or isinstance(r, BaseException):
            # 超时/异常：保留原 paper（synthesizer 会退回 abstract），仅记失败
            updated.append(paper)
            failures.append(paper["arxiv_id"])
            continue
        updated_paper, ok = r
        updated.append(updated_paper)
        if not ok:
            failures.append(updated_paper["arxiv_id"])
    return {"papers": updated, "download_failures": failures}
