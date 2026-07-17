"""清理历史测试数据：删所有 KB（包括 vector collection）+ 所有 doc + 所有 session。

用法：cd backend && uv run python scripts/cleanup_test_data.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal
from app.db.models import Document, KnowledgeBase, MessageModel, SessionModel
from app.rag import vector_store as vs


def main() -> None:
    with SessionLocal() as db:
        kbs = db.query(KnowledgeBase).all()
        for kb in kbs:
            print(f"删除 KB: {kb.name} ({kb.id})")
            vs.delete_collection(kb.id)
        db.query(Document).delete()
        db.query(MessageModel).delete()
        db.query(SessionModel).delete()
        db.query(KnowledgeBase).delete()
        db.commit()
    print("✓ 清理完成")


if __name__ == "__main__":
    main()
