"""FastAPI 应用入口。"""
from __future__ import annotations

import os
import time
import uuid

# ponytail: 进程一启动就强制 HF 离线，避免 BGE 模型加载时联网拉 adapter_config.json
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import structlog  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

from app import __version__  # noqa: E402
from app.api import chat, knowledge, research, sessions  # noqa: E402
from app.config import settings  # noqa: E402
from app.errors import ScholarAIError  # noqa: E402
from app.observability import metrics  # noqa: E402
from app.observability.logging import configure_logging, logger  # noqa: E402
from app.observability.tracing import setup_tracing  # noqa: E402

configure_logging()
setup_tracing()

app = FastAPI(
    title="ScholarAI",
    version=__version__,
    description="一站式论文调研 Agent",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def observe_request(request: Request, call_next):
    """生成 X-Request-ID，绑定 structlog 上下文，并计 Prometheus 请求指标。"""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = rid
    structlog.contextvars.bind_contextvars(request_id=rid)
    t0 = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        # endpoint 用路由模板，避免高基数 label（不把 id 打进去）
        route = request.scope.get("route")
        endpoint = getattr(route, "path", request.url.path)
        metrics.record_request(request.method, endpoint, status, time.perf_counter() - t0)
        structlog.contextvars.clear_contextvars()


@app.exception_handler(ScholarAIError)
async def scholarai_error_handler(request: Request, exc: ScholarAIError):
    rid = getattr(request.state, "request_id", "-")
    logger.warning("api.error", code=exc.code, message=exc.message, path=request.url.path)
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "details": exc.details, "request_id": rid},
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", "-")
    logger.exception("api.unhandled", path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": "internal_error", "message": "服务内部错误", "details": {}, "request_id": rid},
    )


app.include_router(chat.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(research.router, prefix="/api")


@app.get("/metrics")
def prometheus_metrics() -> Response:
    """Prometheus 抓取端点。"""
    payload, content_type = metrics.render()
    return Response(content=payload, media_type=content_type)


@app.get("/health")
def health() -> dict[str, str | bool]:
    """存活探针：进程是否在跑。"""
    return {"status": "alive", "version": __version__}


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "ScholarAI", "docs": "/docs", "version": __version__}

