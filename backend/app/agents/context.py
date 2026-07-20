"""研究请求的可变上下文（M4.5.1）。

每个 /api/research 请求新建一个实例，tool 读写它。
ponytail: 绝不用模块单例——并发请求会串数据。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResearchContext:
    query: str
    session_id: str  # M2: 每 session 独立 PDF 文件夹
    depth: str = "normal"
    max_papers: int = 20
    sub_questions: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    papers: list[dict] = field(default_factory=list)
    analyses: list[dict] = field(default_factory=list)
    draft: str = ""
    feedback: str = ""
    download_failures: list[str] = field(default_factory=list)
    iteration: int = 0
