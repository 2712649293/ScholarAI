"""数据库模型冒烟测试。"""
from __future__ import annotations

from sqlalchemy import inspect

from app.db.models import Document, KnowledgeBase, MessageModel, SessionModel
from app.db.session import Base, engine


def test_all_tables_exist() -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert {"sessions", "messages", "knowledge_bases", "documents"}.issubset(tables)


def test_session_can_be_created_with_messages() -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        s = SessionModel(title="测试", mode="qa")
        db.add(s)
        db.flush()
        db.add(MessageModel(session_id=s.id, role="user", content="hi"))
        db.add(MessageModel(session_id=s.id, role="assistant", content="hello"))
        db.commit()

        loaded = db.get(SessionModel, s.id)
        assert loaded is not None
        assert loaded.title == "测试"
        assert len(loaded.messages) == 2
        assert loaded.messages[0].role == "user"
        assert loaded.messages[1].content == "hello"


def test_kb_can_hold_documents() -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        kb = KnowledgeBase(name=f"kb-test-{id(db)}")
        db.add(kb)
        db.flush()
        db.add(Document(kb_id=kb.id, filename="a.pdf", file_path="a.pdf", status="indexed"))
        db.add(Document(kb_id=kb.id, filename="b.pdf", file_path="b.pdf", status="pending"))
        db.commit()

        loaded = db.get(KnowledgeBase, kb.id)
        assert loaded is not None
        assert len(loaded.documents) == 2
        assert {d.filename for d in loaded.documents} == {"a.pdf", "b.pdf"}


def test_cascade_deletes_messages_with_session() -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        s = SessionModel(title="t")
        db.add(s)
        db.flush()
        sid = s.id
        db.add(MessageModel(session_id=sid, role="user", content="x"))
        db.commit()
        # 删 session
        db.delete(s)
        db.commit()
        # 关联的 message 也应被级联删
        assert db.query(MessageModel).filter_by(session_id=sid).count() == 0
