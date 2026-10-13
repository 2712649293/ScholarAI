"""Durable canonical chunks and SQLite FTS5/BM25 keyword retrieval."""
from __future__ import annotations

import json
import re

import jieba
from sqlalchemy import bindparam, delete, func, select, text
from sqlalchemy.orm import Session

from app.db.models import RAGChunk, RAGGeneration
from app.db.session import SessionLocal

jieba.setLogLevel(30)


def tokenize(value: str) -> str:
    terms: list[str] = []
    for part in re.findall(r"[\u3400-\u9fff]+|[a-zA-Z0-9]+(?:[._+\-][a-zA-Z0-9]+)*", value.lower()):
        terms.extend(jieba.lcut(part) if re.search(r"[\u3400-\u9fff]", part) else [part])
    return " ".join(t for t in terms if t.strip())


def active_generations(kb_ids: list[str]) -> list[RAGGeneration]:
    with SessionLocal() as db:
        return list(db.scalars(select(RAGGeneration).where(RAGGeneration.kb_id.in_(kb_ids))).all())


def chunk_count(kb_id: str) -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(RAGChunk).join(RAGGeneration,
            (RAGChunk.doc_id == RAGGeneration.doc_id) &
            (RAGChunk.generation == RAGGeneration.generation)).where(RAGChunk.kb_id == kb_id)) or 0)


def active_chunks(kb_ids: list[str], chunk_ids: list[str] | None = None) -> dict[str, dict]:
    with SessionLocal() as db:
        statement = select(RAGChunk).join(RAGGeneration,
            (RAGChunk.doc_id == RAGGeneration.doc_id) &
            (RAGChunk.generation == RAGGeneration.generation)).where(RAGChunk.kb_id.in_(kb_ids))
        if chunk_ids is not None:
            statement = statement.where(RAGChunk.chunk_id.in_(chunk_ids))
        rows = db.scalars(statement).all()
        return {row.chunk_id: {"chunk_id": row.chunk_id, "doc_id": row.doc_id,
            "kb_id": row.kb_id, "text": row.text, **json.loads(row.metadata_json)} for row in rows}


def search(kb_ids: list[str], query: str, limit: int) -> list[str]:
    terms = list(dict.fromkeys(tokenize(query).split()))[:64]
    if not terms or not kb_ids:
        return []
    match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    statement = text("""
        SELECT c.chunk_id FROM rag_fts
        JOIN rag_chunks c ON c.chunk_id = rag_fts.chunk_id
        JOIN rag_generations g ON g.doc_id = c.doc_id AND g.generation = c.generation
        WHERE rag_fts MATCH :query AND c.kb_id IN :kb_ids
        ORDER BY bm25(rag_fts), c.chunk_id LIMIT :limit
    """).bindparams(bindparam("kb_ids", expanding=True))
    with SessionLocal() as db:
        return list(db.execute(statement, {"query": match, "kb_ids": kb_ids, "limit": limit}).scalars())


def delete_kb(kb_id: str, session: Session | None = None) -> None:
    if session is not None:
        session.execute(delete(RAGChunk).where(RAGChunk.kb_id == kb_id))
        session.execute(delete(RAGGeneration).where(RAGGeneration.kb_id == kb_id))
        return
    with SessionLocal() as db:
        delete_kb(kb_id, db)
        db.commit()
