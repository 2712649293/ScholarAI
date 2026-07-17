"""Downloader 节点：并发下载论文 PDF（M4.1）。"""
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
_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)


@retry(
    stop=stop_after_attempt(3),  # 1 次 + 2 次重试
    wait=wait_exponential(min=1, max=8),
    retry=retry_if_exception_type(_RETRYABLE),
    reraise=True,
)
async def _fetch(client: httpx.AsyncClient, url: str) -> bytes:
    r = await client.get(url, follow_redirects=True, timeout=30.0)
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

    dest_dir = Path(settings.paper_storage_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_download_one(client, sem, p, dest_dir) for p in papers)
        )

    updated = [p for p, _ in results]
    failures = [p["arxiv_id"] for p, ok in results if not ok]
    return {"papers": updated, "download_failures": failures}
