"""Prometheus 指标（M5.3）。"""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUESTS = Counter(
    "scholarai_requests_total", "HTTP 请求总数", ["method", "endpoint", "status"]
)
DURATION = Histogram(
    "scholarai_request_duration_seconds", "HTTP 请求耗时", ["method", "endpoint"]
)
LLM_TOKENS = Counter("scholarai_llm_tokens_total", "LLM token 用量", ["model", "type"])
TOOL_DURATION = Histogram("scholarai_tool_duration_seconds", "工具(子agent)耗时", ["tool"])


def record_request(method: str, endpoint: str, status: int, seconds: float) -> None:
    REQUESTS.labels(method, endpoint, str(status)).inc()
    DURATION.labels(method, endpoint).observe(seconds)


def record_llm_tokens(model: str, input_tokens: int, output_tokens: int) -> None:
    if input_tokens:
        LLM_TOKENS.labels(model, "input").inc(input_tokens)
    if output_tokens:
        LLM_TOKENS.labels(model, "output").inc(output_tokens)


def record_tool_duration(tool: str, seconds: float) -> None:
    TOOL_DURATION.labels(tool).observe(seconds)


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
