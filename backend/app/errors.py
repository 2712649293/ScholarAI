"""统一异常体系（§8.1）。后续阶段会按需扩展。"""
from __future__ import annotations


class ScholarAIError(Exception):
    code: str = "internal_error"
    status_code: int = 500

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class LLMUnavailable(ScholarAIError):
    code, status_code = "llm_unavailable", 503


class LLMRateLimited(ScholarAIError):
    code, status_code = "llm_rate_limited", 429


class LLMTimeout(ScholarAIError):
    code, status_code = "llm_timeout", 504


class InvalidQuery(ScholarAIError):
    code, status_code = "invalid_query", 400


class ResourceLimitExceeded(ScholarAIError):
    code, status_code = "resource_limit_exceeded", 413
