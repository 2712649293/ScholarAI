"""Embedding 封装：本地 BGE 或 OpenAI-compatible 远程服务。"""
from __future__ import annotations

# ponytail: 必须在 import sentence_transformers 之前强制设 OFFLINE。
# 用 = 而不是 setdefault，因为 shell 可能设了 HF_HUB_OFFLINE=0 / 空。
# 同时也覆盖 TRANSFORMERS_OFFLINE（transformers 库读这个）。
import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from functools import lru_cache  # noqa: E402
import math  # noqa: E402
from typing import Any  # noqa: E402

from openai import OpenAI  # noqa: E402

from app.config import settings  # noqa: E402


@lru_cache(maxsize=1)
def get_model() -> Any:
    """单例 BGE 模型。首次调用会从本地 HF cache 加载。"""
    # 延迟导入，让远程 embedding 部署不必初始化 sentence-transformers。
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.bge_model, device=settings.bge_device)


def _provider() -> str:
    return settings.embedding_provider.strip().lower()


def embedding_model_name() -> str:
    """返回当前 provider 的模型名，记录到知识库元数据中。"""
    if _provider() == "bge":
        return settings.bge_model
    return settings.embedding_model


@lru_cache(maxsize=1)
def _get_remote_client(api_key: str, base_url: str) -> OpenAI:
    if not api_key:
        raise ValueError(
            "远程 embedding 已启用，但 EMBEDDING_API_KEY（或 OPENAI_API_KEY）为空"
        )
    kwargs: dict[str, str] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _remote_client() -> OpenAI:
    return _get_remote_client(settings.embedding_api_key, settings.embedding_base_url)


def _normalize(vectors: list[list[float]]) -> list[list[float]]:
    """统一归一化，保持本地 BGE 与远程向量在 cosine 检索下行为一致。"""
    normalized: list[list[float]] = []
    for vector in vectors:
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            raise ValueError("embedding 服务返回了零向量")
        normalized.append([value / norm for value in vector])
    return normalized


def _embed_remote(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    response = _remote_client().embeddings.create(
        model=settings.embedding_model,
        input=texts,
    )
    # OpenAI-compatible 服务通常按 index 返回，但排序可以兼容乱序响应。
    data = sorted(response.data, key=lambda item: item.index)
    if len(data) != len(texts):
        raise RuntimeError(
            f"embedding 服务返回数量不匹配：请求 {len(texts)} 条，返回 {len(data)} 条"
        )
    return _normalize([[float(value) for value in item.embedding] for item in data])


def embed_texts(texts: list[str]) -> list[list[float]]:
    """把文本列表编码为向量列表（已 normalize，方便 cosine 相似度）。"""
    if _provider() in {"openai", "qwen", "remote", "openai-compatible"}:
        return _embed_remote(texts)
    if _provider() != "bge":
        raise ValueError(
            f"不支持的 EMBEDDING_PROVIDER={settings.embedding_provider!r}，"
            "可选：bge、openai、qwen、remote"
        )
    vecs = get_model().encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


def embed_query(text: str) -> list[float]:
    """单条 query 编码。"""
    return embed_texts([text])[0]


def vector_dim() -> int:
    """当前 embedding 模型的输出维度。"""
    if _provider() in {"openai", "qwen", "remote", "openai-compatible"}:
        # 一些服务拒绝空字符串，因此使用普通探测文本。
        vectors = _embed_remote(["embedding dimension probe"])
        if not vectors:
            raise RuntimeError("embedding 服务未返回向量")
        return len(vectors[0])
    m = get_model()
    # ponytail: 新旧 API 都支持
    fn = getattr(m, "get_embedding_dimension", None) or m.get_sentence_embedding_dimension
    return int(fn())
