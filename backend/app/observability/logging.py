"""结构化日志：structlog JSON → stdout，request_id 用 contextvars 贯通（M5.1）。"""
from __future__ import annotations

import logging

import structlog

from app.config import settings


def configure_logging() -> None:
    """配置 structlog 输出 JSON 行。幂等，可重复调用。"""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,  # 合入 bind_contextvars（request_id 等）
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


logger = structlog.get_logger("scholarai")
