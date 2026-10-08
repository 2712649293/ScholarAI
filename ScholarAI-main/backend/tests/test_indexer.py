"""索引器测试：生成一个临时 PDF，跑完整 index → search 流程。"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pymupdf

from app.rag import indexer


def _make_pdf(path: Path, pages_text: list[str]) -> None:
    """用 pymupdf 生成多页 PDF。"""
    doc = pymupdf.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((50, 50), text, fontsize=11)
    doc.save(str(path))
    doc.close()


def test_index_and_search_small_pdf() -> None:
    kb_id = f"test-kb-{uuid.uuid4().hex[:8]}"
    pages_text = [
        # 第 1 页：RAG 介绍
        "Retrieval Augmented Generation (RAG) combines a retriever with a language model.\n"
        "The retriever fetches relevant documents from a knowledge base.\n"
        "RAG improves factual accuracy and reduces hallucination.",
        # 第 2 页：LangChain 介绍
        "LangChain is a framework for building applications with large language models.\n"
        "It provides chains, agents, and memory components.\n"
        "LangGraph is an extension for stateful multi-actor applications.",
    ]
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "test.pdf"
        _make_pdf(pdf_path, pages_text)

        result = indexer.index_pdf(kb_id, str(pdf_path))
        assert result.page_count == 2
        assert result.chunk_count > 0
        assert result.doc_id

        # 搜 RAG 相关，应命中第 1 页
        hits = indexer.search(kb_id, "RAG 检索增强", k=3)
        assert len(hits) > 0
        assert any("RAG" in h["text"] or "Retrieval" in h["text"] for h in hits)
        assert all(h["score"] >= 0 for h in hits)

        # 搜 LangChain 相关，应命中第 2 页
        hits2 = indexer.search(kb_id, "LangGraph 多 agent", k=3)
        assert any("LangGraph" in h["text"] for h in hits2)


def test_search_empty_kb_returns_empty() -> None:
    kb_id = f"empty-kb-{uuid.uuid4().hex[:8]}"
    hits = indexer.search(kb_id, "anything", k=5)
    assert hits == []
