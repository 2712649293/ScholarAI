"""Business regression tests for layout, optional enrichment and dual-index RAG."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pymupdf
import pytest
from sqlalchemy import select, text

from app.config import settings
from app.db.models import Document, KnowledgeBase, RAGChunk, RAGGeneration
from app.db.session import SessionLocal
from app.errors import RetrievalUnavailable
from app.rag import indexer, keyword_store, vision
from app.rag.markdown_pdf import Figure, ParsedPDF, parse_pdf
from app.rag.pdf_loader import PageText
from app.rag.rerank import rerank
from app.rag.semantic_chunker import split_markdown
from app.rag.tokens import context_tokens


def make_pdf(path: Path, value: str = "RareTechnicalTerm-42 RAG retrieval study.") -> None:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 50), value)
        pdf.save(path)


def unique_kb() -> str:
    return "rag-test-" + uuid.uuid4().hex[:12]


def test_layout_two_columns_vector_figure_and_no_ocr(tmp_path):
    path = tmp_path / "columns.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((50, 50), "A scientific paper", fontsize=18)
        left = "\n".join(f"Left column paragraph {i}. Retrieval studies." for i in range(12))
        right = "\n".join(f"Right column paragraph {i}. Evaluation data." for i in range(12))
        page.insert_textbox(pymupdf.Rect(40, 100, 285, 420), left, fontsize=11)
        page.insert_textbox(pymupdf.Rect(315, 100, 560, 420), right, fontsize=11)
        page.draw_rect(pymupdf.Rect(90, 460, 500, 590), color=(1, 0, 0))
        page.draw_line((110, 565), (460, 480), color=(0, 0, 1))
        page.insert_text((100, 625), "Figure 1: Performance curve.")
        pdf.save(path)
    import pymupdf4llm
    original = pymupdf4llm.to_markdown
    with patch("app.rag.markdown_pdf.pymupdf4llm.to_markdown", wraps=original) as extract:
        parsed = parse_pdf(path, tmp_path / "parsed")
    assert extract.call_args.kwargs["use_ocr"] is False
    assert extract.call_args.kwargs["force_ocr"] is False
    result = parsed.pages[0].text
    assert result.index("Left column paragraph 11") < result.index("Right column paragraph 0")
    assert len(parsed.figures) >= 1  # vector drawing, no embedded raster images
    figure = parsed.figures[0]
    assert Path(figure.path).is_file() and figure.bbox and figure.page == 1
    assert "figure:p1-fig1" in result and "images/" in result
    assert "Performance curve" in result
    assert (tmp_path / "parsed" / "paper.raw.md").exists()


def test_short_page_header_is_preserved(tmp_path):
    path = tmp_path / "short.pdf"
    make_pdf(path)
    assert "RareTechnicalTerm-42" in parse_pdf(path, tmp_path / "out").pages[0].text


def sample_figure(tmp_path) -> tuple[ParsedPDF, Figure]:
    asset = tmp_path / "figure.png"
    asset.write_bytes(b"fake image used only by mocked API")
    figure = Figure("p1-fig1", 1, str(asset), [0, 0, 100, 100], "Figure 1: Accuracy.", "Evaluation.")
    parsed = ParsedPDF([PageText(1, "<!-- figure:p1-fig1 -->\n![p1-fig1](images/figure.png)\n\nFigure 1: Accuracy.")], [figure])
    return parsed, figure


@pytest.mark.asyncio
async def test_vision_unconfigured_makes_no_request(tmp_path, monkeypatch):
    parsed, figure = sample_figure(tmp_path)
    monkeypatch.setattr(settings, "vision_base_url", "")
    with patch.object(vision, "_describe", new=AsyncMock()) as call:
        await vision.enrich(parsed, tmp_path / "cache")
    call.assert_not_called()
    assert figure.status == "skipped" and "Figure 1" in parsed.pages[0].text


@pytest.mark.asyncio
async def test_vision_success_cache_context_invalidation_and_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vision_base_url", "https://vision.invalid/v1")
    parsed, figure = sample_figure(tmp_path)
    with patch.object(vision, "_describe", new=AsyncMock(return_value="准确率随样本增加而提高。")) as call:
        await vision.enrich(parsed, tmp_path / "cache")
        assert figure.status == "generated"
        assert "AI 图片描述" in parsed.pages[0].text and "Figure 1" in parsed.pages[0].text
        cached = replace(figure, status="skipped", description="")
        await vision.enrich(ParsedPDF([PageText(1, "text")], [cached]), tmp_path / "cache")
        assert cached.status == "cached"
        assert call.await_count == 1
        changed = replace(figure, context="Changed neighboring context", description="")
        await vision.enrich(ParsedPDF([], [changed]), tmp_path / "cache")
        assert call.await_count == 2
    failed = replace(figure, context="Failure", description="")
    broken = ParsedPDF([PageText(1, "Original caption")], [failed])
    with patch.object(vision, "_describe", new=AsyncMock(side_effect=httpx.ReadTimeout("timeout"))):
        await vision.enrich(broken, tmp_path / "cache")
    assert failed.status == "failed" and broken.pages[0].text == "Original caption" and broken.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["responses", "chat_completions"])
async def test_vision_protocol_sends_image_and_model(tmp_path, monkeypatch, style):
    monkeypatch.setattr(settings, "vision_base_url", "https://vision.invalid/v1")
    monkeypatch.setattr(settings, "vision_api_style", style)
    _, figure = sample_figure(tmp_path)
    captured = {}
    def handler(request):
        captured["json"] = json.loads(request.content)
        if style == "responses":
            return httpx.Response(200, json={"output": [{"content": [{"type": "output_text", "text": "Description"}]}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Description"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await vision._describe(client, figure) == "Description"
    assert captured["json"]["model"] == "gpt-6.1-sol"
    assert "data:image/png;base64," in json.dumps(captured["json"])
    assert "Accuracy" in json.dumps(captured["json"])


@pytest.mark.asyncio
async def test_vision_document_deadline_cancels_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vision_base_url", "https://vision.invalid/v1")
    monkeypatch.setattr(settings, "vision_document_budget_seconds", .01)
    parsed, figure = sample_figure(tmp_path)
    async def slow(*args):
        await asyncio.sleep(1)
        return "too late"
    with patch.object(vision, "_describe", side_effect=slow):
        await vision.enrich(parsed, tmp_path / "cache")
    assert figure.status == "timeout" and not figure.description and parsed.warnings


def test_chunks_section_cross_page_provenance_and_figure_group(fake_embeddings):
    pages = [PageText(1, "# Method\n\nRAG retrieves evidence.\n\nSecond paragraph."),
        PageText(2, "Continuation of the method.\n\n<!-- figure:p2-fig1 -->\n![p2-fig1](images/a.png)\n\n> [AI 图片描述 · model] A visible curve.\n\n**Figure 1: Accuracy.**\n\n# Results\n\nLangGraph results.")]
    chunks, _ = split_markdown(pages, indexer.embed_texts)
    assert all(c.token_count <= 450 for c in chunks)
    assert any(c.page == 1 and c.page_end == 2 for c in chunks)
    assert all(not ("Method" in c.section and "LangGraph results" in c.text) for c in chunks)
    figure = next(c for c in chunks if c.figure_ids)
    assert "visible curve" in figure.text and "Accuracy" in figure.text and "images/a.png" in figure.text
    assert figure.figure_ids == ["p2-fig1"]
    assert any(s["page"] == 2 and s["line_start"] > 0 for s in figure.source_spans)


def test_semantic_boundary_and_fallback_preserve_text(fake_embeddings, monkeypatch):
    monkeypatch.setattr(settings, "chunk_min_tokens", 1)
    monkeypatch.setattr(settings, "chunk_overlap_tokens", 0)
    pages = [PageText(1, "# Topic\n\nAlpha retrieval paragraph.\n\nBeta retrieval paragraph.\n\nGamma unrelated astronomy.")]
    chunks, _ = split_markdown(pages, lambda texts: [[1, 0], [1, 0], [0, 1]])
    assert len(chunks) == 2
    assert "Alpha" in chunks[0].text and "Gamma" in chunks[1].text
    def broken(texts):
        raise RuntimeError("unavailable")
    fallback, warnings = split_markdown(pages, broken)
    assert "降级" in warnings[0]
    assert all(word in " ".join(c.text for c in fallback) for word in ["Alpha", "Beta", "Gamma"])


def test_long_table_repeats_headers_and_stays_within_limit(fake_embeddings, monkeypatch):
    monkeypatch.setattr(settings, "chunk_max_tokens", 90)
    monkeypatch.setattr(settings, "chunk_overlap_tokens", 0)
    table = "| Model | Value |\n| --- | --- |\n" + "\n".join(f"| Model-{i} | value-{i} |" for i in range(50))
    chunks, _ = split_markdown([PageText(1, "# Results\n\n" + table)], indexer.embed_texts)
    assert len(chunks) > 1
    assert all(c.token_count <= 90 and "| Model | Value |" in c.text for c in chunks)
    assert all(f"value-{i}" in "\n".join(c.text for c in chunks) for i in range(50))


def test_keyword_only_match_scope_and_safe_query(tmp_path, fake_embeddings, monkeypatch):
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    kid, other = unique_kb(), unique_kb()
    result = indexer.index_pdf(kid, str(path))
    indexer.index_pdf(other, str(path))
    with patch.object(indexer, "embed_texts", side_effect=RuntimeError("vector down")):
        hits = indexer.search_many([kid], 'RareTechnicalTerm-42 " OR *', k=5)
    assert hits and all(h["kb_id"] == kid for h in hits)
    assert all(h["doc_id"] == result.doc_id for h in hits)
    assert "vector_unavailable" in hits[0]["retrieval_warnings"]
    assert keyword_store.search([kid], "(((***", 30) == []
    assert keyword_store.tokenize("中文检索 GPT-6.1-sol") == keyword_store.tokenize("中文检索 gpt-6.1-sol")
    with patch.object(keyword_store, "search", side_effect=RuntimeError("FTS down")):
        assert indexer.search(kid, "RAG")
    with patch.object(keyword_store, "search", side_effect=RuntimeError()), patch.object(indexer, "embed_texts", side_effect=RuntimeError()):
        with pytest.raises(RetrievalUnavailable):
            indexer.search(kid, "RAG")


def test_reindex_is_idempotent_and_failed_publication_keeps_previous(tmp_path, fake_embeddings):
    kid, did = unique_kb(), uuid.uuid4().hex
    path = tmp_path / "paper.pdf"
    make_pdf(path, "OriginalUniqueTerm evidence.")
    indexer.index_pdf(kid, str(path), doc_id=did)
    original = keyword_store.active_generations([kid])[0].generation
    make_pdf(path, "ReplacementUniqueTerm evidence.")
    with patch.object(indexer.keyword_store, "tokenize", side_effect=RuntimeError("SQL staging failed")):
        with pytest.raises(RuntimeError):
            indexer.index_pdf(kid, str(path), doc_id=did)
    assert keyword_store.active_generations([kid])[0].generation == original
    assert keyword_store.search([kid], "OriginalUniqueTerm", 30)
    assert not keyword_store.search([kid], "ReplacementUniqueTerm", 30)
    result = indexer.index_pdf(kid, str(path), doc_id=did)
    assert keyword_store.active_generations([kid])[0].generation != original
    assert not keyword_store.search([kid], "OriginalUniqueTerm", 30)
    assert keyword_store.search([kid], "ReplacementUniqueTerm", 30)
    assert indexer.vs.collection_count(kid) == result.chunk_count
    with SessionLocal() as db:
        assert len(list(db.scalars(select(RAGChunk).where(RAGChunk.doc_id == did)))) == result.chunk_count


def test_rrf_deduplicates_before_rerank_and_global_limit(tmp_path, fake_embeddings, monkeypatch):
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    kids = [unique_kb(), unique_kb()]
    for kid in kids:
        indexer.index_pdf(kid, str(path))
    monkeypatch.setattr(settings, "retrieval_branch_limit", 1)
    captured = []
    def capture(query, hits):
        captured.extend(hits)
        return hits
    with patch.object(indexer, "rerank", side_effect=capture):
        hits = indexer.search_many(kids, "RAG", k=1)
    assert len(hits) == 1 and len(captured) <= 2
    assert len({h["chunk_id"] for h in captured}) == len(captured)
    assert max(h["score"] for h in captured) <= 2 / 61


@pytest.mark.parametrize("results", [
    [{"index": 1, "relevance_score": .99}, {"index": 0, "relevance_score": .1}],
    [{"index": 99, "relevance_score": 1}],
    [{"index": 0, "relevance_score": .9}, {"index": 0, "relevance_score": .8}],
    [], [{"index": 0, "relevance_score": "bad"}],
])
def test_external_rerank_and_invalid_response_fallback(monkeypatch, results):
    monkeypatch.setattr(settings, "rerank_endpoint", "https://rerank.invalid/v1/rerank")
    hits = [{"text": "first", "chunk_id": "a"}, {"text": "second", "chunk_id": "b"}]
    response = httpx.Response(200, json={"results": results}, request=httpx.Request("POST", settings.rerank_endpoint))
    with patch("app.rag.rerank.httpx.Client.post", return_value=response) as call:
        output = rerank("question", hits)
    assert call.call_args.kwargs["json"]["documents"] == ["first", "second"]
    if results and results[0]["index"] == 1:
        assert output[0]["chunk_id"] == "b" and output[0]["rerank_score"] == .99
    else:
        assert output == hits
    with patch("app.rag.rerank.httpx.Client.post", side_effect=httpx.ReadTimeout("timeout")):
        assert rerank("question", hits) == hits


def test_delete_document_removes_both_indexes_and_artifacts(client, fake_embeddings, tmp_path):
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    kid = client.post("/api/knowledge", json={"name": unique_kb()}).json()["id"]
    response = client.post(f"/api/knowledge/{kid}/docs", files={"file": ("paper.pdf", path.read_bytes(), "application/pdf")})
    did = response.json()["id"]
    state = client.get(f"/api/knowledge/{kid}/docs/{did}").json()
    assert state["status"] == "indexed", state
    assert indexer.artifact_root(did).is_dir()
    assert client.post(f"/api/knowledge/{kid}/docs/{did}/retry").status_code == 202
    assert client.delete(f"/api/knowledge/{kid}/docs/{did}").status_code == 204
    assert not indexer.artifact_root(did).exists()
    assert indexer.vs.collection_count(kid) == 0
    assert keyword_store.search([kid], "RareTechnicalTerm-42", 30) == []
    assert not keyword_store.active_generations([kid])
    with SessionLocal() as db:
        assert db.get(Document, did) is None
        assert db.execute(text("SELECT count(*) FROM rag_fts WHERE chunk_id LIKE :id"), {"id": did + ":%"}).scalar() == 0
    assert client.get(f"/api/knowledge/{kid}").json()["doc_count"] == 0
    client.delete(f"/api/knowledge/{kid}")


def test_interrupted_jobs_become_retryable():
    from app.rag.cleanup import recover_interrupted_documents
    with SessionLocal() as db:
        kb = KnowledgeBase(name=unique_kb(), embedding_model=indexer.embedding_model_name())
        db.add(kb); db.flush()
        doc = Document(kb_id=kb.id, filename="pending.pdf", file_path="missing.pdf", status="pending", processing_stage="vision")
        db.add(doc); db.commit()
        did, kid = doc.id, kb.id
    assert recover_interrupted_documents() >= 1
    with SessionLocal() as db:
        doc = db.get(Document, did)
        assert doc.status == "failed" and doc.processing_stage == "interrupted"
        db.delete(db.get(KnowledgeBase, kid)); db.commit()


def test_qa_context_retains_evidence_beyond_old_300_char_cutoff(monkeypatch):
    from app.api.chat import _build_rag_context
    evidence = "Long lead-in. " * 45 + "CRITICAL_EVIDENCE_AT_END"
    hit = {"kb_id": "k", "doc_id": "d", "text": evidence, "page": 1, "page_end": 2,
        "section": "Results", "figure_ids": ["p2-fig1"], "chunk_index": 0, "score": .03}
    with patch.object(indexer, "search_many", return_value=[hit]):
        context, citations = _build_rag_context(["k"], "question")
    assert "CRITICAL_EVIDENCE_AT_END" in context
    assert citations[0].page_end == 2 and citations[0].figure_ids == ["p2-fig1"]
    assert context_tokens(context) <= settings.rag_context_tokens


def test_embedding_uses_local_tokenizer_and_rejects_truncation(monkeypatch):
    from types import SimpleNamespace
    from app.rag import embeddings
    from app.rag.tokens import embedding_counter
    model = SimpleNamespace(tokenizer=SimpleNamespace(encode=lambda value, **kw: list(value)),
        max_seq_length=64, encode=lambda *args, **kw: pytest.fail("must reject before model.encode"))
    monkeypatch.setattr(settings, "embedding_provider", "bge")
    monkeypatch.setattr(embeddings, "get_model", lambda: model)
    with patch("app.rag.tokens.get_model", return_value=model):
        count, limit = embedding_counter()
        assert count("中文abc") == 5 and limit == 64
        with pytest.raises(ValueError, match="静默截断"):
            embeddings.embed_texts(["a" * 65])


def test_changed_embedding_endpoint_uses_keywords_instead_of_old_vectors(tmp_path, fake_embeddings, monkeypatch):
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    kid = unique_kb()
    indexer.index_pdf(kid, str(path))
    monkeypatch.setattr(settings, "embedding_base_url", "https://different.invalid/v1")
    hits = indexer.search(kid, "RareTechnicalTerm-42")
    assert hits and "vector_unavailable" in hits[0]["retrieval_warnings"]


def test_sql_commit_failure_keeps_active_generation_and_rolls_back_fts(tmp_path, fake_embeddings):
    from sqlalchemy.orm import Session
    from sqlalchemy import event
    path = tmp_path / "paper.pdf"
    make_pdf(path, "OriginalBeforeCommit RAG evidence.")
    kid, did = unique_kb(), uuid.uuid4().hex
    indexer.index_pdf(kid, str(path), doc_id=did)
    original = keyword_store.active_generations([kid])[0].generation
    make_pdf(path, "NewAfterCommit RAG evidence.")
    def reject_publication(session):
        if any(isinstance(row, RAGGeneration) for row in session.dirty):
            raise RuntimeError("publication failed")
    event.listen(Session, "before_commit", reject_publication)
    try:
        with pytest.raises(RuntimeError, match="publication failed"):
            indexer.index_pdf(kid, str(path), doc_id=did)
    finally:
        event.remove(Session, "before_commit", reject_publication)
    assert keyword_store.active_generations([kid])[0].generation == original
    assert keyword_store.search([kid], "OriginalBeforeCommit", 30)
    assert not keyword_store.search([kid], "NewAfterCommit", 30)
    assert len(indexer.vs.get_or_create_collection(kid).get(include=[])["ids"]) == keyword_store.chunk_count(kid)


def test_very_long_section_keeps_full_metadata_and_unicode_body(fake_embeddings, monkeypatch):
    monkeypatch.setattr(settings, "chunk_max_tokens", 64)
    monkeypatch.setattr(settings, "chunk_overlap_tokens", 0)
    heading = "Very long scientific title " * 50
    content = "中文正文需要保留原始字符。" * 60
    chunks, warnings = split_markdown([PageText(1, "# " + heading + "\n\n" + content)], indexer.embed_texts)
    assert all(c.section == heading.strip() for c in chunks)
    assert all(c.token_count <= 64 and "�" not in c.text for c in chunks)
    assert warnings
    bodies = "".join(c.text.split("\n\n", 1)[1] for c in chunks)
    assert bodies == content


def test_neighboring_image_paths_do_not_invalidate_vision_context_cache(tmp_path):
    path = tmp_path / "paper.pdf"
    make_pdf(path)
    def extraction(pdf, **kwargs):
        image_dir = Path(kwargs["image_path"])
        first, second = image_dir / "a.png", image_dir / "b.png"
        first.write_bytes(b"first image")
        second.write_bytes(b"second image")
        return [{"metadata": {"page_number": 1}, "page_boxes": [], "text":
            f"Evidence.\n\n![]({first})\n\nFigure 1: First diagram.\n\n![]({second})\n\nFigure 2: Second diagram."}]
    with patch("app.rag.markdown_pdf.pymupdf4llm.to_markdown", side_effect=extraction):
        first = parse_pdf(path, tmp_path / "generation1")
        second = parse_pdf(path, tmp_path / "generation2")
    assert len(first.figures) == 2
    assert [f.context for f in first.figures] == [f.context for f in second.figures]
    assert all(str(tmp_path) not in f.context for f in first.figures)
    assert first.figures[0].caption == "Figure 1: First diagram."


@pytest.mark.asyncio
async def test_kb_deleted_during_streamed_upload_does_not_leave_orphan(tmp_path, monkeypatch):
    from app.api.knowledge import upload_doc
    from fastapi import BackgroundTasks, HTTPException
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    with SessionLocal() as db:
        kb = KnowledgeBase(name=unique_kb(), embedding_model=indexer.embedding_model_name())
        db.add(kb); db.commit(); kid = kb.id
    class DisappearingKBUpload:
        filename = "test.pdf"
        delivered = False
        async def read(self, size):
            if self.delivered:
                return b""
            self.delivered = True
            with SessionLocal() as db:
                db.delete(db.get(KnowledgeBase, kid)); db.commit()
            return b"%PDF-valid-header"
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as caught:
            await upload_doc(kid, BackgroundTasks(), DisappearingKBUpload(), db)
    assert caught.value.status_code == 404
    assert list(tmp_path.glob("*.pdf")) == []
    with SessionLocal() as db:
        assert not list(db.scalars(select(Document).where(Document.kb_id == kid)))
