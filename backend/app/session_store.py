"""内存版 session/message 存储（M1.4 占位，M2.1 替换为 SQLite）。"""
from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Deque


@dataclass
class Message:
    role: str  # 'user' | 'assistant' | 'system'
    content: str


@dataclass
class Session:
    id: str
    title: str = "新对话"
    mode: str = "qa"
    messages: Deque[Message] = field(default_factory=lambda: deque(maxlen=20))


class InMemorySessionStore:
    """线程不安全的单进程内存版存储；够 dev 用。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str | None, *, mode: str = "qa") -> Session:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        new_id = session_id or uuid.uuid4().hex
        s = Session(id=new_id, mode=mode)
        self._sessions[new_id] = s
        return s

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def add_message(self, session_id: str, role: str, content: str) -> None:
        s = self._sessions.get(session_id)
        if s is None:
            return
        s.messages.append(Message(role=role, content=content))

    def recent(self, session_id: str, n: int = 10) -> list[Message]:
        s = self._sessions.get(session_id)
        if s is None:
            return []
        return list(s.messages)[-n:]


# ponytail: 单例
store = InMemorySessionStore()
