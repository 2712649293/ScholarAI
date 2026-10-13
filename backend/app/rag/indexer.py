"""PDF → Markdown/optional vision → semantic chunks → published dual indexes."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from functools import lru_cache
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
from threading import RLock
from typing import cast
import uuid

from chromadb.api.types import Embeddings, Where
from sqlalchemy import delete, select

from app.config import settings
from app.db.models import Document, KnowledgeBase, RAGChunk, RAGGeneration
from app.db.session import SessionLocal
from app.errors import RetrievalUnavailable
from app.rag import keyword_store, vector_store as vs, vision
from app.rag.embeddings import embed_texts, embedding_model_name
from app.rag.markdown_pdf import parse_pdf
from app.rag.rerank import rerank
from app.rag.semantic_chunker import split_markdown

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def kb_lock(kb_id: str) -> RLock:
    # This application uses one worker; the lock covers indexing/deletion races.
    return RLock()


@dataclass
class IndexResult:
    doc_id: str
    page_count: int
    chunk_count: int
    warnings: list[str] = field(default_factory=list)


def artifact_root(doc_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", doc_id):
        raise ValueError("invalid document id")
    return Path(settings.upload_dir) / "rag" / doc_id


def _stage(doc_id: str, stage: str) -> None:
    with SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc:
            doc.processing_stage = stage
            db.commit()


def index_pdf(kb_id: str, pdf_path: str, *, doc_id: str | None = None,
              require_existing: bool = False) -> IndexResult:
    did = doc_id or uuid.uuid4().hex
    with kb_lock(kb_id):
        with SessionLocal() as db:
            if require_existing and (db.get(KnowledgeBase, kb_id) is None or db.get(Document, did) is None):
                raise ValueError("知识库或文档已删除")
        return _index(kb_id, pdf_path, did)


def _index(kb_id: str, pdf_path: str, did: str) -> IndexResult:
    generation = uuid.uuid4().hex
    root = artifact_root(did)
    directory = root / generation
    ids: list[str] = []
    coll = None
    published = False
    try:
        _stage(did, "parsing")
        parsed = parse_pdf(pdf_path, directory)
        # A figure-only scan is not treated as textual input when OCR is disabled.
        text_layer = "\n".join(re.sub(r"<!--.*?-->|!\[[^\]]*\]\([^)]*\)", "", p.text) for p in parsed.pages)
        if not text_layer.strip():
            raise ValueError("PDF 未提取到文本，本版本未启用 OCR；请使用有文本层的 PDF")
        _stage(did, "vision")
        asyncio.run(vision.enrich(parsed, root / "vision-cache"))
        parsed.save(directory, enriched=True)
        _stage(did, "chunking")
        chunks, warnings = split_markdown(parsed.pages, embed_texts)
        parsed.warnings.extend(warnings)
        if not chunks:
            raise ValueError("PDF 没有可索引正文")
        _stage(did, "embedding")
        vectors = embed_texts([c.text for c in chunks])
        if len(vectors) != len(chunks) or not vectors or not vectors[0]:
            raise ValueError("embedding 返回数量或维度不合法")
        if any(len(v) != len(vectors[0]) for v in vectors):
            raise ValueError("embedding 向量维度不一致")
        fingerprint = json.dumps({"parser": "pymupdf4llm-1.28.2", "chunker": "markdown-semantic-v1",
            "embedding": embedding_model_name(), "provider": settings.embedding_provider,
            "endpoint_hash": hashlib.sha256(settings.embedding_base_url.encode()).hexdigest(),
            "dimension": len(vectors[0]), "chunk_limits": [settings.chunk_min_tokens,
                settings.chunk_target_tokens, settings.chunk_max_tokens, settings.chunk_overlap_tokens],
            "input_limit": settings.embedding_max_input_tokens}, sort_keys=True)
        _stage(did, "indexing")
        parsed.save(directory, enriched=True)
        (directory / "chunks.json").write_text(json.dumps({
            "doc_id": did, "kb_id": kb_id, "generation": generation,
            "fingerprint": json.loads(fingerprint), "chunks": [asdict(c) for c in chunks],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        coll = vs.get_or_create_collection(kb_id)
        ids = [f"{did}:{generation}:{c.chunk_index}" for c in chunks]
        batch_size = vs.get_client().get_max_batch_size()
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start:start + batch_size]
            coll.add(ids=ids[start:start + batch_size], embeddings=cast(Embeddings, vectors[start:start + batch_size]),
                documents=[c.text for c in batch], metadatas=[{"doc_id": did, "generation": generation,
                    "page": c.page, "page_end": c.page_end, "chunk_index": c.chunk_index} for c in batch])
        if len(coll.get(where={"generation": generation}, include=[])["ids"]) != len(chunks):
            raise ValueError("向量索引写入不完整")
        with SessionLocal() as db:
            previous = db.get(RAGGeneration, did)
            if previous and previous.kb_id != kb_id:
                raise ValueError("文档已属于其他知识库")
            previous_generation = previous.generation if previous else None
            # FTS triggers and publication pointer commit in one SQL transaction.
            db.execute(delete(RAGChunk).where(RAGChunk.doc_id == did))
            for cid, chunk in zip(ids, chunks):
                metadata = asdict(chunk)
                metadata.pop("text")
                db.add(RAGChunk(chunk_id=cid, kb_id=kb_id, doc_id=did, generation=generation,
                    text=chunk.text, terms=keyword_store.tokenize(chunk.text), metadata_json=json.dumps(metadata, ensure_ascii=False)))
            db.flush()
            if previous:
                previous.generation = generation
                previous.fingerprint = fingerprint
            else:
                db.add(RAGGeneration(doc_id=did, kb_id=kb_id, generation=generation, fingerprint=fingerprint))
            doc = db.get(Document, did)
            if doc:
                doc.processing_stage = "complete"
                doc.warnings = json.dumps(parsed.warnings, ensure_ascii=False)
            db.commit()
        published = True
        if previous_generation:
            try:
                coll.delete(where={"generation": previous_generation})
                shutil.rmtree(root / previous_generation, ignore_errors=True)
            except Exception:
                logger.warning("Obsolete vectors retained; unpublished generation is excluded from search")
        return IndexResult(did, len(parsed.pages), len(chunks), parsed.warnings)
    except Exception:
        if not published:
            if coll is not None and ids:
                try:
                    coll.delete(ids=ids)
                except Exception:
                    logger.warning("Failed generation vectors remain hidden")
            shutil.rmtree(directory, ignore_errors=True)
        raise


def search(kb_id: str, query: str, *, k: int = 5) -> list[dict]:
    return search_many([kb_id], query, k=k)


def search_many(kb_ids: list[str], query: str, *, k: int = 6) -> list[dict]:
    kb_ids = list(dict.fromkeys(kb_ids))
    if not kb_ids:
        return []
    limit = settings.retrieval_branch_limit
    vector_ids: list[str] = []
    keyword_ids: list[str] = []
    vector_failed = keyword_failed = False
    try:
        generations = keyword_store.active_generations(kb_ids)
        if generations:
            q_vec = embed_texts([query])[0]
            scored: list[tuple[str, float]] = []
            succeeded = False
            for kid in kb_ids:
                active = [g.generation for g in generations if g.kb_id == kid]
                # Same model name on a different provider/endpoint/dimension is
                # not evidence that its vectors can share this query embedding.
                compatible: list[str] = []
                for g in generations:
                    if g.kb_id != kid:
                        continue
                    signature = json.loads(g.fingerprint)
                    if (signature.get("embedding") == embedding_model_name()
                        and signature.get("provider") == settings.embedding_provider
                        and signature.get("dimension") == len(q_vec)
                        and signature.get("endpoint_hash") == hashlib.sha256(settings.embedding_base_url.encode()).hexdigest()):
                        compatible.append(g.generation)
                if set(compatible) != set(active):
                    vector_failed = True
                    logger.warning("Incompatible embedding generation excluded; keyword retrieval remains available")
                active = compatible
                if not active:
                    continue
                try:
                    coll = vs.get_or_create_collection(kid)
                    res = coll.query(query_embeddings=cast(Embeddings, [q_vec]), n_results=min(limit, coll.count()),
                        where=cast(Where, {"generation": {"$in": active}}), include=["distances"])
                    succeeded = True
                    distances = res["distances"]
                    if distances is not None:
                        scored.extend(zip(res["ids"][0], distances[0]))
                except Exception:
                    vector_failed = True
                    logger.warning("Vector retrieval failed for one knowledge base")
            if not succeeded:
                raise RuntimeError("no vector collection available")
            vector_ids = [cid for cid, _ in sorted(scored, key=lambda p: p[1])[:limit]]
    except Exception:
        vector_failed = True
        logger.warning("Vector retrieval failed; using keyword retrieval")
    try:
        keyword_ids = keyword_store.search(kb_ids, query, limit)
    except Exception:
        keyword_failed = True
        logger.warning("Keyword retrieval failed; using vector retrieval")
    if vector_failed and keyword_failed:
        raise RetrievalUnavailable("关键词与向量检索均不可用，请稍后重试")
    scores: dict[str, float] = {}
    for branch in (vector_ids, keyword_ids):
        for rank, cid in enumerate(branch, 1):
            scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
    if not scores:
        return []
    try:
        canonical = keyword_store.active_chunks(kb_ids, list(scores))
    except Exception as exc:
        raise RetrievalUnavailable("检索来源索引不可用，请稍后重试") from exc
    ordered = sorted(scores, key=lambda cid: (-scores[cid], cid))
    hits = [{**canonical[cid], "score": scores[cid],
             "retrieval_warnings": (["vector_unavailable"] if vector_failed else []) +
                                   (["keyword_unavailable"] if keyword_failed else [])}
            for cid in ordered if cid in canonical][:settings.rerank_candidate_limit]
    return rerank(query, hits)[:k]
