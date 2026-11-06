"""论文检索核心，使用 paper-search-mcp MCP 作为唯一外部来源。"""

from app.search.models import PaperRecord, SearchResult
from app.search.service import SearchAgent

__all__ = ["PaperRecord", "SearchAgent", "SearchResult"]
