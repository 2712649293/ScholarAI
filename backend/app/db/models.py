"""ORM 模型：会话、消息、知识库、文档。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
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
    status: Mapped[str] = mapped_column(String(20), default="idle")  # idle|running|done|failed
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    kb: Mapped[KnowledgeBase] = relationship(back_populates="documents")
