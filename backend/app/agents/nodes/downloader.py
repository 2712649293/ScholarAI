"""Downloader 节点：通过 paper-search-mcp 的 OA 回退链下载 PDF。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.agents.state import ResearchContext
from app.config import settings
from app.search.mcp_client import PaperSearchMCP, PaperSearchMCPError
from app.search.models import paper_key

MAX_CONCURRENT = 5
DOWNLOAD_TOTAL_TIMEOUT = 240  # 全部下载总超时（秒）；到点未完成的算失败，防 httpx 卡死
PDF_MAX_BYTES = 50 * 1024 * 1024


def _failure_id(paper: dict) -> str:
    """Use the canonical key for new records and raw arXiv ids for legacy ones."""
    return str(paper.get("paper_key") or paper.get("arxiv_id") or paper_key(paper))


def _file_id(paper: dict) -> str:
    """Keep legacy arXiv filenames stable while supporting other sources."""
    return str(paper.get("paper_key") or paper.get("arxiv_id") or paper_key(paper))


async def _download_one(
    client: PaperSearchMCP, sem: asyncio.Semaphore, paper: dict, dest_dir: Path
) -> tuple[dict, bool]:
    path = dest_dir / f"{_file_id(paper).replace('/', '_').replace(':', '_')}.pdf"
    if path.exists() and path.stat().st_size > 0:  # 断点：已下过就跳过
        return {**paper, "local_path": str(path), "download_status": "cached"}, True
    async with sem:
        try:
            source = str((paper.get("sources") or ["arxiv"])[0])
            paper_id = str(
                (paper.get("source_ids") or {}).get(source)
                or paper.get("arxiv_id")
                or paper.get("doi")
                or paper.get("paper_key")
                or ""
            )
            returned_path = await client.download(
                source=source,
                paper_id=paper_id,
                doi=str(paper.get("doi") or ""),
                title=str(paper.get("title") or ""),
                save_path=str(dest_dir),
            )
        except PaperSearchMCPError:
            return {**paper, "download_status": "failed", "download_error": "fetch_failed"}, False
    returned = Path(returned_path.strip())
    if not returned.is_absolute():
        returned = dest_dir / returned
    try:
        returned.resolve().relative_to(dest_dir.resolve())
    except ValueError:
        return {**paper, "download_status": "failed", "download_error": "path_outside_session"}, False
    if not returned.exists() or returned.stat().st_size <= 0:
        return {**paper, "download_status": "failed", "download_error": "empty_file"}, False
    if returned.stat().st_size > PDF_MAX_BYTES:
        return {**paper, "download_status": "failed", "download_error": "file_too_large"}, False
    try:
        prefix = returned.read_bytes()[:5]
    except OSError:
        return {**paper, "download_status": "failed", "download_error": "read_failed"}, False
    if prefix != b"%PDF-":
        return {**paper, "download_status": "failed", "download_error": "invalid_pdf"}, False
    # Normalize the MCP server's filename to the stable session-local key.
    if returned != path:
        path.write_bytes(returned.read_bytes())
        try:
            returned.unlink()
        except OSError:
            pass
    return {**paper, "local_path": str(path), "download_status": "downloaded"}, True


async def run(state: ResearchContext) -> ResearchContext:
    papers = state.get("papers", [])
    if not papers:
        return {"papers": [], "download_failures": []}

    # M2: 每 session 独立 PDF 目录（删 session 时一起删）
    session_id = state.get("session_id")
    base_dir = Path(settings.paper_storage_dir)
    dest_dir = base_dir / session_id if session_id else base_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    selection_active = "selected_paper_keys" in state
    selected = {str(key) for key in (state.get("selected_paper_keys") or [])}
    selected_papers: list[dict] = []
    skipped_papers: list[dict] = []
    for paper in papers:
        canonical_key = paper_key(paper)
        legacy_key = str(paper.get("arxiv_id") or "")
        declared_key = str(paper.get("paper_key") or "")
        matches_selection = bool({canonical_key, legacy_key, declared_key} & selected)
        if selection_active and not matches_selection:
            skipped_papers.append({**paper, "download_status": "not_selected"})
        else:
            selected_papers.append(paper)
    if not selected_papers:
        return {"papers": skipped_papers, "download_failures": []}
    client = PaperSearchMCP()
    # M5.5.11: asyncio.wait 替代 wait_for —— 超时时保留 done 任务的结果，
    # 不丢弃已成功写入磁盘的 PDF（实测 gather 被 timeout kill 后全部丢弃）。
    tasks = {
        asyncio.ensure_future(_download_one(client, sem, p, dest_dir)): p
        for p in selected_papers
    }
    done, pending = await asyncio.wait(tasks, timeout=DOWNLOAD_TOTAL_TIMEOUT)

    # 取消未完成的任务（CancelledError 不进入 except Exception，semaphore 自动释放）
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    updated: list[dict] = list(skipped_papers)
    failures: list[str] = []
    for task, paper in tasks.items():
        if task in done:
            try:
                updated_paper, ok = task.result()
                updated.append(updated_paper)
                if not ok:
                    failures.append(_failure_id(updated_paper))
            except Exception:
                # 极小概率：任务 done 但 result() 抛异常
                updated.append(paper)
                failures.append(_failure_id(paper))
        else:
            # 超时未完成 → 保留原 paper（synthesizer 退 abstract）
            updated.append(paper)
            failures.append(_failure_id(paper))
    return {"papers": updated, "download_failures": failures}
