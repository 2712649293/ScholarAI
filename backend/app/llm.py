"""LLM 工厂 + 带超时/重试的调用封装（§8.2）。"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

import openai
from langchain_openai import ChatOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.errors import LLMTimeout, LLMUnavailable, LLMRateLimited

# 仅对网络/服务端错误重试；4xx/超时/限流抛业务异常不重试
RETRYABLE = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    """单例 ChatModel。DeepSeek 兼容 OpenAI 接口，直接用 ChatOpenAI。"""
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=0.3,
        timeout=settings.llm_timeout_seconds,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_exception_type(RETRYABLE),
    reraise=True,
)
async def call_llm(messages: list[Any], *, timeout: float | None = None) -> str:
    """异步调 LLM，返回文本结果。带超时 + 3 次重试。"""
    llm = get_llm()
    t = timeout or settings.llm_timeout_seconds
    try:
        result = await asyncio.wait_for(llm.ainvoke(messages), timeout=t)
        return result.content if hasattr(result, "content") else str(result)
    except asyncio.TimeoutError as e:
        raise LLMTimeout(f"LLM 调用超时（{t}s）") from e
    except openai.RateLimitError as e:
        raise LLMRateLimited("LLM 触发限流") from e
    except openai.APIError as e:
        raise LLMUnavailable(f"LLM 服务异常：{e}") from e
