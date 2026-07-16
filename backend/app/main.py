"""FastAPI 应用入口。"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import chat
from app.config import settings
from app.errors import ScholarAIError

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
async def add_request_id(request: Request, call_next):
    """每个请求生成 X-Request-ID，便于链路追踪。"""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


@app.exception_handler(ScholarAIError)
async def scholarai_error_handler(request: Request, exc: ScholarAIError):
    rid = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "details": exc.details, "request_id": rid},
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=500,
        content={"code": "internal_error", "message": "服务内部错误", "details": {}, "request_id": rid},
    )


app.include_router(chat.router, prefix="/api")


@app.get("/health")
def health() -> dict[str, str | bool]:
    """存活探针：进程是否在跑。"""
    return {"status": "alive", "version": __version__}


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "ScholarAI", "docs": "/docs", "version": __version__}
