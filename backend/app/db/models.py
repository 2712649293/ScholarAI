"""ORM 模型：会话、消息、知识库、文档。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DDL, DateTime, ForeignKey, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    mode: Mapped[str] = mapped_column(String(20), default="qa")
    status: Mapped[str] = mapped_column(
        String(20), default="idle"
    )  # idle|running|done|failed|needs_review|timed_out|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    messages: Mapped[list["MessageModel"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", lazy="selectin"
    )


class MessageModel(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # user|assistant|system|tool
    content: Mapped[str] = mapped_column(Text)
    extra: Mapped[str | None] = mapped_column(Text, default=None)  # JSON 字符串
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[SessionModel] = relationship(back_populates="messages")


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    embedding_model: Mapped[str] = mapped_column(String(100), default="bge-small-zh-v1.5")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    doc_count: Mapped[int] = mapped_column(Integer, default=0)
    vector_count: Mapped[int] = mapped_column(Integer, default=0)

    documents: Mapped[list["Document"]] = relationship(
        back_populates="kb", cascade="all, delete-orphan", lazy="selectin"
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    kb_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(500))
    file_path: Mapped[str] = mapped_column(String(1000))  # 相对 PAPER_STORAGE_DIR
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|indexed|failed
    error: Mapped[str | None] = mapped_column(Text, default=None)
    processing_stage: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending")
    warnings: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    kb: Mapped[KnowledgeBase] = relationship(back_populates="documents")


class RAGGeneration(Base):
    """Publication pointer. Both indexes are written before this is switched."""
    __tablename__ = "rag_generations"
    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(64), index=True)
    generation: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(Text)


class RAGChunk(Base):
    __tablename__ = "rag_chunks"
    chunk_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(64), index=True)
    kb_id: Mapped[str] = mapped_column(String(64), index=True)
    generation: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(Text)
    terms: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text)


# Keep FTS in the same SQLite transaction as canonical chunks, including deletes.
FTS_DDL = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS rag_fts USING fts5(chunk_id UNINDEXED, terms)",
    "CREATE TRIGGER IF NOT EXISTS rag_chunks_ai AFTER INSERT ON rag_chunks BEGIN "
    "INSERT INTO rag_fts(chunk_id, terms) VALUES (new.chunk_id, new.terms); END",
    "CREATE TRIGGER IF NOT EXISTS rag_chunks_ad AFTER DELETE ON rag_chunks BEGIN "
    "DELETE FROM rag_fts WHERE chunk_id = old.chunk_id; END",
    "CREATE TRIGGER IF NOT EXISTS rag_chunks_au AFTER UPDATE ON rag_chunks BEGIN "
    "DELETE FROM rag_fts WHERE chunk_id = old.chunk_id; "
    "INSERT INTO rag_fts(chunk_id, terms) VALUES (new.chunk_id, new.terms); END",
]
for ddl in FTS_DDL:
    event.listen(RAGChunk.__table__, "after_create", DDL(ddl).execute_if(dialect="sqlite"))


# M4.5.1: research_states 表已删——workflow 状态由 langgraph checkpointer 持久化（thread_id=session_id）。
# 旧 §12.11 的 ResearchStateModel 不再需要，alembic 迁移见 a3f5d8e2b1c4_drop_research_states.py
