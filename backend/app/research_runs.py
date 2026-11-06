"""Single-worker research operations with detached SSE subscribers.

The registry is intentionally process-local for the current single-worker
deployment. It owns every research phase (clarify, plan and execution), so a
request can disconnect without cancelling the operation that owns the
checkpoint.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
import json

from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.db.models import SessionModel
from app.db.session import SessionLocal
from app.observability.logging import logger


@dataclass
class ResearchRun:
    kind: str = "execute"
    events: list[tuple[str, dict]] = field(default_factory=list)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None
    finished: bool = False
    error: str | None = None
    error_code: str | None = None
    result: dict | None = None
    cancel_requested: bool = False
    previous_status: str = "idle"

    async def emit(self, event: str, data: dict) -> None:
        async with self.changed:
            self.events.append((event, data))
            self.changed.notify_all()


# shortcut: single worker, use a durable task queue before scaling to multiple workers.
runs: dict[str, ResearchRun] = {}


def _read_status(sid: str) -> str:
    with SessionLocal() as db:
        session = db.get(SessionModel, sid)
        if session is None:
            raise HTTPException(404, "会话不存在")
        if session.mode != "research":
            raise HTTPException(409, "会话不是研究模式")
        return session.status


def _status(sid: str, status: str) -> None:
    with SessionLocal() as db:
        session = db.get(SessionModel, sid)
        if session:
            session.status = status
            db.commit()


def ensure_idle(sid: str) -> None:
    run = runs.get(sid)
    if run and not run.finished:
        raise HTTPException(409, "研究正在执行，请等待完成")


def claim(sid: str, kind: str, *, start_status: str | None = None) -> ResearchRun:
    """Claim a session before any awaited checkpoint operation."""
    ensure_idle(sid)
    run = ResearchRun(kind=kind, previous_status=_read_status(sid))
    if start_status is not None:
        _status(sid, start_status)
    runs[sid] = run
    return run


def finish_claim(run: ResearchRun, *, status: str | None = None) -> None:
    """Release a synchronous claim used by plan mutations or validation."""
    for sid, current in list(runs.items()):
        if current is run:
            _status(sid, run.previous_status if status is None else status)
            run.finished = True
            runs.pop(sid, None)
            return


def recover_interrupted() -> None:
    with SessionLocal() as db:
        for session in db.scalars(select(SessionModel).where(
            SessionModel.mode == "research", SessionModel.status == "running"
        )):
            session.status = "failed"
        db.commit()


def start(
    sid: str,
    work: Callable[[ResearchRun], Awaitable[dict]],
    *,
    kind: str = "execute",
    existing: ResearchRun | None = None,
    success_status: str = "done",
    final_event: str | None = "final",
    timeout_seconds: float | None = None,
) -> ResearchRun:
    run = existing or claim(sid, kind, start_status="running")
    if runs.get(sid) is not run or run.finished:
        raise HTTPException(409, "研究正在执行，请等待完成")
    _status(sid, "running")
    budget = settings.research_timeout_seconds if timeout_seconds is None else timeout_seconds

    async def execute() -> None:
        logger.info("research.started", session_id=sid, kind=run.kind)
        try:
            final = await asyncio.wait_for(work(run), timeout=budget)
            run.result = final
            status = str(final.get("_session_status", success_status))
            _status(sid, status)
            if final_event is not None:
                await run.emit(final_event, final)
            logger.info("research.completed", session_id=sid, kind=run.kind, status=status)
        except asyncio.TimeoutError:
            run.error_code = "research_timeout"
            run.error = f"研究超过 {budget:g} 秒，已停止，可从检查点继续"
            _status(sid, "timed_out")
            await run.emit("error", {"code": run.error_code, "message": run.error})
            logger.warning("research.timed_out", session_id=sid, kind=run.kind)
        except asyncio.CancelledError:
            run.error_code = "cancelled" if run.cancel_requested else "interrupted"
            run.error = (
                "研究已取消"
                if run.cancel_requested
                else "研究因服务停止而中断，可从检查点继续"
            )
            _status(sid, "cancelled" if run.cancel_requested else "failed")
            await run.emit("error", {"code": run.error_code, "message": run.error})
            logger.warning(
                "research.cancelled" if run.cancel_requested else "research.interrupted",
                session_id=sid,
                kind=run.kind,
            )
            raise
        except Exception as exc:
            run.error_code = getattr(exc, "code", "internal_error")
            run.error = str(exc)
            _status(sid, "failed")
            await run.emit("error", {"code": run.error_code, "message": run.error})
            logger.exception("research.failed", session_id=sid, kind=run.kind)
        finally:
            async with run.changed:
                run.finished = True
                run.changed.notify_all()

    # Keep the task owned by the application, not StreamingResponse's cancel scope.
    run.task = asyncio.create_task(execute(), name=f"research:{sid}:{run.kind}")
    return run


async def stream(sid: str, run: ResearchRun) -> AsyncIterator[str]:
    offset = 0
    try:
        while True:
            while offset < len(run.events):
                event, data = run.events[offset]
                offset += 1
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            if run.finished:
                yield ": done\n\n"
                return
            try:
                async with run.changed:
                    await asyncio.wait_for(run.changed.wait_for(
                        lambda: len(run.events) > offset or run.finished
                    ), timeout=15)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
    finally:
        if not run.finished:
            logger.info("research.stream_detached", session_id=sid, kind=run.kind)


async def cancel(sid: str, *, reason: str = "shutdown", discard: bool = True) -> None:
    run = runs.get(sid)
    if run and run.task and not run.finished:
        run.cancel_requested = reason == "user"
        run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)
        # A task cancelled before its first scheduling turn never enters
        # execute(), so its CancelledError handler cannot publish the state.
        if reason == "user" and not run.finished:
            run.error_code = "cancelled"
            run.error = "研究已取消"
            _status(sid, "cancelled")
            await run.emit("error", {"code": run.error_code, "message": run.error})
            async with run.changed:
                run.finished = True
                run.changed.notify_all()
    if discard:
        runs.pop(sid, None)


async def shutdown() -> None:
    for sid in list(runs):
        await cancel(sid, reason="shutdown", discard=True)
