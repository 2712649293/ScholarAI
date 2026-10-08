"""LangChain 回调：把 agent 的 tool / LLM 事件转成结构化日志 + Prometheus 指标（M5.1/M5.3）。

单例 HANDLER 挂到所有 LLM/agent 调用；request_id 由 contextvars 在事件时读取，
所以一个全局实例即可，无需每请求新建。
"""
from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from app.config import settings
from app.observability import metrics
from app.observability.logging import logger


def _extract_tokens(response: LLMResult) -> tuple[int, int]:
    """从 LLMResult 取 (input_tokens, output_tokens)，兼容 OpenAI/DeepSeek 与 usage_metadata。"""
    usage = (response.llm_output or {}).get("token_usage") if response.llm_output else None
    if usage:
        return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    try:
        msg = response.generations[0][0].message  # type: ignore[attr-defined]
        um = getattr(msg, "usage_metadata", None) or {}
        return um.get("input_tokens", 0), um.get("output_tokens", 0)
    except Exception:
        return 0, 0


class ObservabilityHandler(BaseCallbackHandler):
    def __init__(self) -> None:
        self._tools: dict[UUID, tuple[str, float]] = {}

    def on_tool_start(self, serialized: dict, input_str: str, *, run_id: UUID, **kw: Any) -> None:
        name = (serialized or {}).get("name", "tool")
        self._tools[run_id] = (name, time.perf_counter())
        logger.info("tool.start", tool=name)

    def on_tool_end(self, output: Any, *, run_id: UUID, **kw: Any) -> None:
        name, t0 = self._tools.pop(run_id, ("tool", time.perf_counter()))
        dur = time.perf_counter() - t0
        metrics.record_tool_duration(name, dur)
        logger.info("tool.end", tool=name, duration=round(dur, 3))

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kw: Any) -> None:
        pt, ct = _extract_tokens(response)
        metrics.record_llm_tokens(settings.llm_model, pt, ct)
        logger.info("llm.call", model=settings.llm_model, input_tokens=pt, output_tokens=ct)


# 全局单例：挂到 agent config 和 call_llm
HANDLER = ObservabilityHandler()
