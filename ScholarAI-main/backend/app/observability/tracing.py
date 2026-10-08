"""LangSmith tracing 集成（M5.2）。开则设 env，不开不报错（优雅降级）。"""
from __future__ import annotations

import os

from app.observability.logging import logger
from app.config import settings


def setup_tracing() -> None:
    if not settings.langsmith_tracing:
        return
    if not settings.langsmith_api_key:
        logger.warning("tracing.skip", reason="LANGSMITH_TRACING 开启但无 API key")
        return
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
    logger.info("tracing.enabled", project=settings.langsmith_project)
