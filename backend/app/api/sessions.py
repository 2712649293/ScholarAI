"""会话记录 API（M2.6.3）：列表 / 详情 / 删除 / 改名。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SessionModel
from app.db.session import get_db
from app.errors import SessionNotFound
from app.schemas.session import (
    MessageOut,
    SessionDetail,
    SessionSummary,
    SessionUpdate,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _not_found() -> SessionNotFound:
    return SessionNotFound("会话不存在")


def _summary(s: SessionModel) -> SessionSummary:
    # ponytail: len(s.messages) 走 selectin，列表整体一条额外查询，够用
    return SessionSummary(
        id=s.id, title=s.title, mode=s.mode, status=s.status,
        message_count=len(s.messages), created_at=s.created_at, updated_at=s.updated_at,
    )


@router.get("", response_model=list[SessionSummary])
def list_sessions(db: Session = Depends(get_db)) -> list[SessionSummary]:
    rows = db.scalars(select(SessionModel).order_by(SessionModel.updated_at.desc())).all()
    return [_summary(s) for s in rows]


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(session_id: str, db: Session = Depends(get_db)) -> SessionDetail:
    s = db.get(SessionModel, session_id)
    if s is None:
        raise _not_found()
    msgs = sorted(s.messages, key=lambda m: (m.created_at, m.id))
    return SessionDetail(
        **_summary(s).model_dump(),
        messages=[MessageOut.model_validate(m) for m in msgs],
    )


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: str, db: Session = Depends(get_db)) -> None:
    s = db.get(SessionModel, session_id)
    if s is None:
        raise _not_found()
    db.delete(s)  # cascade 删 messages
    db.commit()


@router.patch("/{session_id}", response_model=SessionSummary)
def update_session(session_id: str, req: SessionUpdate, db: Session = Depends(get_db)) -> SessionSummary:
    s = db.get(SessionModel, session_id)
    if s is None:
        raise _not_found()
    s.title = req.title
    db.commit()
    db.refresh(s)
    return _summary(s)
