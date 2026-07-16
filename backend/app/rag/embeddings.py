"""BGE embedding 封装（BAAI/bge-small-zh-v1.5，本地 CPU）。"""
from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import settings


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """单例 BGE 模型。首次调用会从 HF 下载约 100MB。"""
    return SentenceTransformer(settings.bge_model, device=settings.bge_device)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """把文本列表编码为向量列表（已 normalize，方便 cosine 相似度）。"""
    vecs = get_model().encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


def embed_query(text: str) -> list[float]:
    """单条 query 编码。"""
    return embed_texts([text])[0]


def vector_dim() -> int:
    """当前 embedding 模型的输出维度。"""
    m = get_model()
    # ponytail: 新旧 API 都支持
    fn = getattr(m, "get_embedding_dimension", None) or m.get_sentence_embedding_dimension
    return int(fn())
