"""LLM 工厂 + 带超时/重试的调用封装（§8.2）。"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

import openai
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
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
    return _create_llm(
        settings.llm_model, settings.llm_api_key,
        settings.llm_base_url, settings.llm_reasoning_effort,
    )


@lru_cache(maxsize=1)
def get_small_llm() -> ChatOpenAI:
    """论文分析专用模型；未配置模型时复用主模型，不在调用失败后切换。"""
    if not settings.small_llm_model.strip():
        return get_llm()
    base_url = settings.small_llm_base_url.strip() or settings.llm_base_url
    api_key = settings.small_llm_api_key.strip()
    if not api_key:
        if base_url.rstrip("/") != settings.llm_base_url.strip().rstrip("/"):
            raise ValueError("独立 SMALL_LLM_BASE_URL 需要设置 SMALL_LLM_API_KEY（无鉴权服务可填 none）")
        api_key = settings.llm_api_key
    return _create_llm(
        settings.small_llm_model, api_key,
        base_url, settings.small_llm_reasoning_effort,
    )


def _create_llm(model: str, api_key: str, base_url: str, effort: str) -> ChatOpenAI:
    model_name = model.strip()
    reasoning_effort = effort.strip() or None
    # vLLM/Qwen OpenAI-compatible deployments may emit only ``reasoning``
    # when thinking is enabled. LangChain exposes that response with empty
    # ``content``, which breaks planner JSON and normal review text. Disable
    # thinking by default for Qwen unless the operator explicitly overrides it.
    if reasoning_effort is None and model_name.lower().startswith("qwen"):
        reasoning_effort = "none"
    kwargs: dict[str, Any] = {
        "model": model_name,
        "api_key": SecretStr(api_key),
        "base_url": base_url,
        "temperature": 0.3,
        "timeout": settings.llm_timeout_seconds,
    }
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    return ChatOpenAI(
        **kwargs,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_exception_type(RETRYABLE),
    reraise=True,
)
async def call_llm(
    messages: list[Any], *, timeout: float | None = None, use_small_model: bool = False,
) -> str:
    """异步调 LLM，返回文本结果。带超时 + 3 次重试。"""
    llm = get_small_llm() if use_small_model else get_llm()
    t = timeout or settings.llm_timeout_seconds
    from app.observability.callbacks import HANDLER  # 局部导入避免循环

    try:
        result = await asyncio.wait_for(
            llm.ainvoke(messages, config={"callbacks": [HANDLER]}), timeout=t
        )
        return result.text
    except asyncio.TimeoutError as e:
        raise LLMTimeout(f"LLM 调用超时（{t}s）") from e
    except openai.RateLimitError as e:
        raise LLMRateLimited("LLM 触发限流") from e
    except openai.APIError as e:
        raise LLMUnavailable(f"LLM 服务异常：{e}") from e
