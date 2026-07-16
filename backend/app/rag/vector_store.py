"""Chroma 向量库封装：每个 KB 一个 collection，cosine 距离。"""
from __future__ import annotations

from functools import lru_cache

import chromadb

from app.config import settings


@lru_cache(maxsize=1)
def get_client() -> chromadb.api.ClientAPI:
    """单例 Chroma 持久化客户端。"""
    return chromadb.PersistentClient(path=settings.chroma_persist_dir)


def get_or_create_collection(kb_id: str) -> chromadb.api.models.Collection:
    """每个 KB 一个 collection，cosine 距离。"""
    return get_client().get_or_create_collection(
        name=kb_id, metadata={"hnsw:space": "cosine"}
    )


def delete_collection(kb_id: str) -> None:
    """删除整个 collection（KB 删除时调用）。"""
    try:
        get_client().delete_collection(name=kb_id)
    except (ValueError, chromadb.errors.NotFoundError):  # type: ignore[attr-defined]
        pass


def collection_count(kb_id: str) -> int:
    """collection 中向量数。collection 不存在返回 0。"""
    try:
        return get_or_create_collection(kb_id).count()
    except Exception:
        return 0
