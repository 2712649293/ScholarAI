"""启动时清理使用旧 embedding 模型的知识库。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db.models import KnowledgeBase
from app.db.session import SessionLocal
from app.rag import vector_store
from app.rag.embeddings import embedding_model_name


def _is_current_model(stored_model: str, current_model: str) -> bool:
    """兼容早期 BGE 只保存短模型名的知识库记录。"""
    if stored_model == current_model:
        return True
    if settings.embedding_provider.strip().lower() == "bge":
        return stored_model == current_model.rsplit("/", 1)[-1]
    return False


def _remove_document_files(kb: KnowledgeBase) -> None:
    """清理旧知识库的上传文件，只删除应用配置目录下的文件。"""
    roots = [
        Path(settings.upload_dir).resolve(),
        Path(settings.paper_storage_dir).resolve(),
    ]
    for document in kb.documents:
        path = Path(document.file_path).resolve()
        if not any(path == root or root in path.parents for root in roots):
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # 文件清理失败不阻止向量和数据库清理；遗留文件不会被检索。
            continue


def cleanup_stale_knowledge_bases() -> int:
    """删除 embedding 模型不匹配的知识库及其 Chroma collection。

    先删除向量 collection，再提交数据库删除；如果 Chroma 删除失败，数据库
    事务不会提交，避免留下仍可见但无法检索的知识库记录。
    """
    current_model = embedding_model_name()
    removed = 0
    try:
        with SessionLocal() as db:
            knowledge_bases = list(db.scalars(select(KnowledgeBase)).all())
            for kb in knowledge_bases:
                if _is_current_model(kb.embedding_model, current_model):
                    continue
                vector_store.delete_collection(kb.id)
                _remove_document_files(kb)
                db.delete(kb)
                removed += 1
            if removed:
                db.commit()
    except OperationalError:
        # 源码开发模式可能尚未执行 alembic；让服务先启动，迁移后下次启动再清理。
        return 0
    return removed
