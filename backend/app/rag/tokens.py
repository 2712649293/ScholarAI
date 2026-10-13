"""Token accounting: actual local embedding tokenizer, configurable remote limit."""
from __future__ import annotations

from functools import lru_cache
from typing import Callable

import tiktoken

from app.config import settings
from app.rag.embeddings import get_model


@lru_cache(maxsize=1)
def context_encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def context_tokens(text: str) -> int:
    return len(context_encoding().encode(text, disallowed_special=()))


def embedding_counter() -> tuple[Callable[[str], int], int]:
    if settings.embedding_provider.lower() == "bge":
        model = get_model()
        tokenizer = model.tokenizer
        limit = min(settings.embedding_max_input_tokens, int(model.max_seq_length))
        return lambda text: len(tokenizer.encode(text, add_special_tokens=True)), limit
    return context_tokens, settings.embedding_max_input_tokens


def split_to_limit(text: str, limit: int, count: Callable[[str], int]) -> list[str]:
    """Split at Unicode boundaries; never decode partial UTF-8 token sequences."""
    result: list[str] = []
    while text:
        if count(text) <= limit:
            result.append(text)
            break
        lo, hi = 1, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if count(text[:mid]) <= limit:
                lo = mid
            else:
                hi = mid - 1
        if count(text[:lo]) > limit:
            raise ValueError("token budget cannot fit a single character")
        # Prefer a complete sentence or word near the hard boundary.
        import re
        boundaries = list(re.finditer(r"[。！？.!?]\s*|\s+", text[:lo]))
        cut = boundaries[-1].end() if boundaries and boundaries[-1].end() > lo // 2 else lo
        result.append(text[:cut])
        text = text[cut:]
    return result
