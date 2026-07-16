"""FastAPI 应用入口。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import chat
from app.config import settings

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

app.include_router(chat.router, prefix="/api")


@app.get("/health")
def health() -> dict[str, str | bool]:
    """存活探针：进程是否在跑。"""
    return {"status": "alive", "version": __version__}


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "ScholarAI", "docs": "/docs", "version": __version__}
