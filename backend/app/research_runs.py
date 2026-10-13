"""Single-worker research runs: execution survives SSE subscriber disconnects."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
import json

from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import SessionModel
from app.db.session import SessionLocal
from app.observability.logging import logger


@dataclass
class ResearchRun:
    events: list[tuple[str, dict]] = field(default_factory=list)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None
    finished: bool = False
    error: str | None = None

    async def emit(self, event: str, data: dict) -> None:
        async with self.changed:
            self.events.append((event, data))
            self.changed.notify_all()


# shortcut: single worker, use a durable task queue before scaling to multiple workers.
runs: dict[str, ResearchRun] = {}


def ensure_idle(sid: str) -> None:
    run = runs.get(sid)
    if run and not run.finished:
        raise HTTPException(409, "研究正在执行，请等待完成")


def _status(sid: str, status: str) -> None:
    with SessionLocal() as db:
        session = db.get(SessionModel, sid)
        if session:
            session.status = status
            db.commit()


def recover_interrupted() -> None:
    with SessionLocal() as db:
        for session in db.scalars(select(SessionModel).where(
            SessionModel.mode == "research", SessionModel.status == "running"
        )):
            session.status = "failed"
        db.commit()


def start(sid: str, work: Callable[[ResearchRun], Awaitable[dict]]) -> ResearchRun:
    ensure_idle(sid)
    run = ResearchRun()
    _status(sid, "running")
    runs[sid] = run

    async def execute() -> None:
        logger.info("research.started", session_id=sid)
        try:
            final = await work(run)
            _status(sid, "done")
            await run.emit("final", final)
            logger.info("research.completed", session_id=sid)
        except asyncio.CancelledError:
            run.error = "研究因服务停止而中断，可从检查点继续"
            _status(sid, "failed")
            await run.emit("error", {"message": run.error})
            logger.warning("research.interrupted", session_id=sid)
            raise
        except Exception as exc:
            run.error = str(exc)
            _status(sid, "failed")
            await run.emit("error", {"code": "internal_error", "message": run.error})
            logger.exception("research.failed", session_id=sid)
        finally:
            async with run.changed:
                run.finished = True
                run.changed.notify_all()

    # Keep the task owned by the application, not StreamingResponse's cancel scope.
    run.task = asyncio.create_task(execute(), name=f"research:{sid}")
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
            logger.info("research.stream_detached", session_id=sid)


async def cancel(sid: str) -> None:
    run = runs.get(sid)
    if run and run.task and not run.task.done():
        run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)
    runs.pop(sid, None)


async def shutdown() -> None:
    for sid in list(runs):
        await cancel(sid)
