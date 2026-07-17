"""DB 版 session/message 存储（M2.6.1）。

保持 get_or_create / get / recent / add_message 接口不变，chat.py 零改动。
ponytail: 不做内存缓存——本地 SQLite 够快；要缓存等切 Postgres 再说。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.models import MessageModel, SessionModel
from app.db.session import SessionLocal


@dataclass
class Message:
    role: str  # 'user' | 'assistant' | 'system'
    content: str


@dataclass
class Session:
    id: str
    title: str
    mode: str


class DBSessionStore:
    """写穿透到 DB；每次调用开一个短生命周期 Session。"""

    def get_or_create(self, session_id: str | None, *, mode: str = "qa") -> Session:
        with SessionLocal() as db:
            if session_id:
                s = db.get(SessionModel, session_id)
                if s is not None:
                    return Session(id=s.id, title=s.title, mode=s.mode)
            new_id = session_id or uuid.uuid4().hex
            s = SessionModel(id=new_id, mode=mode)
            db.add(s)
            db.commit()
            db.refresh(s)
            return Session(id=s.id, title=s.title, mode=s.mode)

    def get(self, session_id: str) -> Session | None:
        with SessionLocal() as db:
            s = db.get(SessionModel, session_id)
            return Session(id=s.id, title=s.title, mode=s.mode) if s else None

    def add_message(self, session_id: str, role: str, content: str) -> None:
        with SessionLocal() as db:
            s = db.get(SessionModel, session_id)
            if s is None:
                return
            db.add(MessageModel(session_id=session_id, role=role, content=content))
            # M2.6.2: 首条用户消息截前 20 字作标题（不用 LLM，省 token）
            if role == "user" and s.title == "新对话":
                s.title = content[:20]
            # 每条消息都 bump updated_at，侧边栏才能按最近活跃排序
            s.updated_at = datetime.now(timezone.utc)
            db.commit()

    def recent(self, session_id: str, n: int = 10) -> list[Message]:
        with SessionLocal() as db:
            rows = (
                db.query(MessageModel)
                .filter(MessageModel.session_id == session_id)
                .order_by(MessageModel.created_at.desc(), MessageModel.id.desc())
                .limit(n)
                .all()
            )
            return [Message(role=r.role, content=r.content) for r in reversed(rows)]


# ponytail: 单例
store = DBSessionStore()
