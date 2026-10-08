"""embedding 模型切换时的知识库清理测试。"""
from __future__ import annotations

import uuid

from app.db.models import KnowledgeBase
from app.db.session import SessionLocal
from app.rag import cleanup


def test_cleanup_removes_stale_model_kb(monkeypatch) -> None:
    current_name = cleanup.embedding_model_name()
    suffix = uuid.uuid4().hex
    stale = KnowledgeBase(name=f"stale-embedding-kb-{suffix}", embedding_model="old-model")
    current = KnowledgeBase(name=f"current-embedding-kb-{suffix}", embedding_model=current_name)
    with SessionLocal() as db:
        db.add_all([stale, current])
        db.commit()
        stale_id = stale.id
        current_id = current.id

    deleted_collections: list[str] = []
    monkeypatch.setattr(
        cleanup.vector_store,
        "delete_collection",
        lambda kb_id: deleted_collections.append(kb_id),
    )

    assert cleanup.cleanup_stale_knowledge_bases() >= 1
    assert stale_id in deleted_collections
    assert current_id not in deleted_collections

    with SessionLocal() as db:
        assert db.get(KnowledgeBase, stale_id) is None
        assert db.get(KnowledgeBase, current_id) is not None
        db.delete(db.get(KnowledgeBase, current_id))
        db.commit()
