"""BGE embedding 封装（BAAI/bge-small-zh-v1.5，本地 CPU）。"""
from __future__ import annotations

# ponytail: 必须在 import sentence_transformers 之前强制设 OFFLINE。
# 用 = 而不是 setdefault，因为 shell 可能设了 HF_HUB_OFFLINE=0 / 空。
# 同时也覆盖 TRANSFORMERS_OFFLINE（transformers 库读这个）。
import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from functools import lru_cache  # noqa: E402

from sentence_transformers import SentenceTransformer  # noqa: E402

from app.config import settings  # noqa: E402


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
