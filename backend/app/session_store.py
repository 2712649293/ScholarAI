"""DB 版 session/message 存储（M2.6.1 + M4.5.1）。

保持 get_or_create / get / recent / add_message 接口不变，chat.py 零改动。
M4.5.1: 删了 load/save_research_state（workflow 状态由 langgraph checkpointer 管）。
ponytail: 不做内存缓存——本地 SQLite 够快；要缓存等切 Postgres 再说。
"""
from __future__ import annotations

import uuid
import re
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
        if session_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", session_id):
            from app.errors import InvalidQuery
            raise InvalidQuery("会话 id 必须为1至32位字母、数字、下划线或连字符")
        with SessionLocal() as db:
            if session_id:
                s = db.get(SessionModel, session_id)
                if s is not None:
                    if s.mode != mode:
                        from app.errors import SessionModeConflict
                        raise SessionModeConflict(
                            f"会话已固定为 {s.mode} 模式，不能以 {mode} 模式使用"
                        )
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

    def add_message(self, session_id: str, role: str, content: str) -> str | None:
        with SessionLocal() as db:
            s = db.get(SessionModel, session_id)
            if s is None:
                return None
            message = MessageModel(session_id=session_id, role=role, content=content)
            db.add(message)
            # M2.6.2: 首条用户消息截前 20 字作标题（不用 LLM，省 token）
            if role == "user" and s.title == "新对话":
                s.title = content[:20]
            # 每条消息都 bump updated_at，侧边栏才能按最近活跃排序
            s.updated_at = datetime.now(timezone.utc)
            db.commit()
            return message.id

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

    # === M2 per-session 文件清理（删 session 时同步删 PDF）===

    def delete_files(self, session_id: str) -> None:
        """删 session 的 paper 目录。DB 删除不在此。"""
        import shutil
        from app.config import settings
        from pathlib import Path

        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", session_id):
            return
        root = Path(settings.paper_storage_dir).resolve()
        papers_dir = root / session_id
        if papers_dir.resolve().parent != root:
            return
        if papers_dir.exists() and papers_dir.is_dir():
            shutil.rmtree(papers_dir, ignore_errors=True)
        reports_root = Path(settings.reports_dir).resolve()
        for report in reports_root.glob(f"{session_id}_*.md"):
            if report.resolve().parent == reports_root:
                report.unlink(missing_ok=True)


# ponytail: 单例
store = DBSessionStore()
