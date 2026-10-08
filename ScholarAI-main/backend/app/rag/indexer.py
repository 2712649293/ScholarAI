"""索引流水线：PDF → 文本 → 分块 → embedding → 写入 Chroma。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.rag import vector_store as vs
from app.rag.chunker import Chunk, split_pages
from app.rag.embeddings import embed_texts
from app.rag.pdf_loader import load_pdf


@dataclass
class IndexResult:
    doc_id: str
    page_count: int
    chunk_count: int


def index_pdf(kb_id: str, pdf_path: str, *, doc_id: str | None = None) -> IndexResult:
    """把一个 PDF 索引进指定 KB 的 collection。返回索引结果。"""
    did = doc_id or uuid.uuid4().hex
    pages = load_pdf(pdf_path)
    chunks: list[Chunk] = split_pages(pages)
    if not chunks:
        return IndexResult(doc_id=did, page_count=len(pages), chunk_count=0)

    texts = [c.text for c in chunks]
    vecs = embed_texts(texts)
    coll = vs.get_or_create_collection(kb_id)
    coll.add(
        ids=[f"{did}_p{c.page}_c{c.chunk_index}" for c in chunks],
        embeddings=vecs,
        documents=texts,
        metadatas=[
            {"doc_id": did, "page": c.page, "chunk_index": c.chunk_index}
            for c in chunks
        ],
    )
    return IndexResult(doc_id=did, page_count=len(pages), chunk_count=len(chunks))


def search(kb_id: str, query: str, *, k: int = 5) -> list[dict]:
    """在指定 KB 中检索 top-k 命中。返回 [{text, page, doc_id, score}, ...]"""
    coll = vs.get_or_create_collection(kb_id)
    if coll.count() == 0:
        return []
    q_vec = embed_texts([query])[0]
    res = coll.query(query_embeddings=[q_vec], n_results=min(k, coll.count()))
    hits = []
    for i, doc in enumerate(res["documents"][0]):
        meta = res["metadatas"][0][i]
        dist = res["distances"][0][i] if "distances" in res else 0.0
        hits.append({
            "text": doc,
            "doc_id": meta.get("doc_id"),
            "page": meta.get("page"),
            "chunk_index": meta.get("chunk_index"),
            "score": 1.0 - dist,  # cosine 距离 → 相似度
        })
    return hits
