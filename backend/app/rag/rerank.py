"""Common external /rerank protocol; failure keeps the complete RRF ordering."""
from __future__ import annotations

import logging
import math

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def rerank(query: str, hits: list[dict]) -> list[dict]:
    if not hits or not settings.rerank_endpoint:
        return hits
    payload: dict = {"query": query, "documents": [h["text"] for h in hits], "top_n": len(hits)}
    if settings.rerank_model:
        payload["model"] = settings.rerank_model
    headers = {"Authorization": f"Bearer {settings.rerank_api_key}"} if settings.rerank_api_key else {}
    try:
        with httpx.Client(timeout=settings.rerank_timeout_seconds, headers=headers) as client:
            response = client.post(settings.rerank_endpoint, json=payload)
            response.raise_for_status()
        results = response.json()["results"]
        if not isinstance(results, list) or not results:
            raise ValueError("empty rerank response")
        scores: dict[int, float] = {}
        for item in results:
            index, score = item["index"], item["relevance_score"]
            if type(index) is not int or index < 0 or index >= len(hits) or index in scores:
                raise ValueError("invalid rerank index")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ValueError("invalid rerank score")
            scores[index] = float(score)
        # A partial response must not silently discard unreturned evidence.
        order = sorted(scores, key=lambda i: scores[i], reverse=True)
        return [{**hits[i], "rerank_score": scores[i]} for i in order] + [h for i, h in enumerate(hits) if i not in scores]
    except Exception:
        logger.warning("External rerank unavailable or invalid; using RRF order")
        return hits
