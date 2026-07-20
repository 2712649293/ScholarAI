"""DB 版 session/message 存储（M2.6.1）。

保持 get_or_create / get / recent / add_message 接口不变，chat.py 零改动。
ponytail: 不做内存缓存——本地 SQLite 够快；要缓存等切 Postgres 再说。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.models import MessageModel, ResearchStateModel, SessionModel
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

    # === 研究跨轮记忆（M4.6/§12.11）===

    def load_research_state(self, session_id: str) -> dict | None:
        """读 session 的研究状态。无返回 None。"""
        with SessionLocal() as db:
            rs = db.get(ResearchStateModel, session_id)
            if rs is None:
                return None
            return {
                "papers": json.loads(rs.papers or "[]"),
                "analyses": json.loads(rs.analyses or "[]"),
                "draft": rs.draft or "",
                "feedback": rs.feedback or "",
                "download_failures": json.loads(rs.download_failures or "[]"),
                "search_queries": json.loads(rs.search_queries or "[]"),
                "sub_questions": json.loads(rs.sub_questions or "[]"),
                "iteration": rs.iteration or 0,
            }

    def save_research_state(self, session_id: str, state: dict) -> None:
        """upsert 研究状态。重复 session_id 覆盖（最后一次研究胜出）。"""
        with SessionLocal() as db:
            rs = db.get(ResearchStateModel, session_id)
            payload = {
                "papers": json.dumps(state.get("papers", []), ensure_ascii=False),
                "analyses": json.dumps(state.get("analyses", []), ensure_ascii=False),
                "draft": state.get("draft", ""),
                "feedback": state.get("feedback", ""),
                "download_failures": json.dumps(
                    state.get("download_failures", []), ensure_ascii=False
                ),
                "search_queries": json.dumps(
                    state.get("search_queries", []), ensure_ascii=False
                ),
                "sub_questions": json.dumps(state.get("sub_questions", []), ensure_ascii=False),
                "iteration": state.get("iteration", 0),
            }
            if rs is None:
                db.add(ResearchStateModel(session_id=session_id, **payload))
            else:
                for k, v in payload.items():
                    setattr(rs, k, v)
            db.commit()

    def delete_files(self, session_id: str) -> None:
        """删 session 的 paper 目录（M2 per-session 路径）。DB 删除不在此。"""
        import shutil
        from app.config import settings
        from pathlib import Path

        papers_dir = Path(settings.paper_storage_dir) / session_id
        if papers_dir.exists() and papers_dir.is_dir():
            shutil.rmtree(papers_dir, ignore_errors=True)


# ponytail: 单例
store = DBSessionStore()
