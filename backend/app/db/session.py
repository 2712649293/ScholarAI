"""数据库引擎 + 会话工厂 + FastAPI 依赖。"""
from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _ensure_data_dir(url: str) -> None:
    """SQLite 文件型 db 时确保 data 目录存在。"""
    if url.startswith("sqlite:///"):
        db_path = Path(url.replace("sqlite:///", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)


# ponytail: dev 阶段用 sync 就够。切 Postgres 时把 create_engine 换成 async 引擎即可
engine = create_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
)

_ensure_data_dir(settings.database_url)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每次请求一个 Session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
