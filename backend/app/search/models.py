"""paper-search-mcp 返回结果映射到研究流程使用的稳定数据模型。"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import re
from typing import Any, TypedDict, cast


class PaperRecord(TypedDict, total=False):
    """跨 MCP 来源的论文记录。

    保留 arxiv_id 是为了兼容已有 checkpoint 和综述引用；新来源使用
    paper_key/source_ids/doi 作为身份信息。
    """

    paper_key: str
    title: str
    authors: list[str]
    year: int
    abstract: str
    doi: str | None
    arxiv_id: str | None
    source_ids: dict[str, str]
    sources: list[str]
    pdf_url: str | None
    landing_url: str | None
    is_open_access: bool
    provider_score: float | None
    relevance_score: float | None
    local_path: str | None


@dataclass(frozen=True)
class SearchResult:
    papers: list[PaperRecord]
    queries: list[str]
    sources: list[str]
    source_errors: dict[str, str]
    iterations: int
    status: str


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = value.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix):]
    return doi.lower().strip() or None


def paper_key(paper: Mapping[str, Any]) -> str:
    doi = normalize_doi(paper.get("doi"))
    if doi:
        return f"doi:{doi}"
    if paper.get("arxiv_id"):
        arxiv = re.sub(r"v\d+$", "", str(paper["arxiv_id"]).strip().lower())
        return f"arxiv:{arxiv}"
    source_ids = paper.get("source_ids") or {}
    if source_ids:
        source, identifier = sorted(source_ids.items())[0]
        return f"{source}:{identifier}".lower()
    title = " ".join(str(paper.get("title") or "").lower().split())
    authors = " ".join((paper.get("authors") or [])[:1]).lower()
    return f"title:{title}|author:{authors}|year:{paper.get('year', 0)}"


def paper_identity_keys(paper: Mapping[str, Any]) -> tuple[str, ...]:
    """Return all stable identities available for cross-source matching.

    One MCP source may expose a DOI while another only exposes an arXiv id or a
    bibliographic title.  Keeping aliases lets the search service merge those
    records without putting source-specific logic into the research graph.
    """
    keys: list[str] = []
    doi = normalize_doi(paper.get("doi"))
    if doi:
        keys.append(f"doi:{doi}")
    arxiv_id = paper.get("arxiv_id")
    if arxiv_id:
        normalized_arxiv = re.sub(r"v\d+$", "", str(arxiv_id).strip().lower())
        keys.append(f"arxiv:{normalized_arxiv}")
    title = " ".join(str(paper.get("title") or "").lower().split())
    authors = " ".join((paper.get("authors") or [])[:1]).lower()
    if title:
        keys.append(f"title:{title}|author:{authors}|year:{paper.get('year', 0)}")
    if not keys:
        keys.append(paper_key(paper))
    return tuple(dict.fromkeys(keys))


def citation_id(paper: Mapping[str, Any]) -> str:
    """给综述和审校使用的稳定短引用标识。"""
    return str(paper.get("arxiv_id") or normalize_doi(paper.get("doi")) or paper_key(paper))


def merge_paper(existing: PaperRecord, incoming: PaperRecord) -> PaperRecord:
    """合并同论文的来源、链接和下载路径。"""
    merged: dict[str, Any] = dict(existing)
    for key, value in incoming.items():
        if value not in (None, "", [], {}):
            merged[key] = value
    merged["paper_key"] = paper_key(merged)
    merged["sources"] = sorted(set(existing.get("sources", [])) | set(incoming.get("sources", [])))
    merged["source_ids"] = {**existing.get("source_ids", {}), **incoming.get("source_ids", {})}
    if existing.get("local_path"):
        merged["local_path"] = existing["local_path"]
    return cast(PaperRecord, merged)
