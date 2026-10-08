"""M2.6 会话记录 API + 持久化 + 自动标题测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.session_store import store

client = TestClient(app)


def _make_session_with_two_msgs() -> str:
    with patch("app.api.chat.call_llm", new=AsyncMock(return_value="回答")):
        r = client.post("/api/chat/qa", json={"query": "什么是 RAG 检索增强生成技术的核心思想呢"})
    return r.json()["session_id"]


def test_auto_title_from_first_message_no_llm() -> None:
    sid = _make_session_with_two_msgs()
    r = client.get(f"/api/sessions/{sid}")
    assert r.status_code == 200
    # 截前 20 字，不调用 LLM
    assert r.json()["title"] == "什么是 RAG 检索增强生成技术的核心思想"[:20]


def test_list_sessions_contains_new_one() -> None:
    sid = _make_session_with_two_msgs()
    r = client.get("/api/sessions")
    assert r.status_code == 200
    rows = {s["id"]: s for s in r.json()}
    assert sid in rows
    assert rows[sid]["message_count"] == 2


def test_get_session_returns_ordered_messages() -> None:
    sid = _make_session_with_two_msgs()
    r = client.get(f"/api/sessions/{sid}")
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


def test_persistence_survives_store_reinstantiation() -> None:
    """写穿透到 DB：新建 store 实例仍能读到历史（模拟重启）。"""
    sid = _make_session_with_two_msgs()
    from app.session_store import DBSessionStore
    assert len(DBSessionStore().recent(sid, n=20)) == 2


def test_rename_session() -> None:
    sid = _make_session_with_two_msgs()
    r = client.patch(f"/api/sessions/{sid}", json={"title": "改个名"})
    assert r.status_code == 200
    assert r.json()["title"] == "改个名"
    assert client.get(f"/api/sessions/{sid}").json()["title"] == "改个名"


def test_delete_session_cascades_messages() -> None:
    sid = _make_session_with_two_msgs()
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert store.recent(sid, n=20) == []


def test_active_session_bubbles_to_top() -> None:
    """加消息应 bump updated_at，活跃会话重新排到列表顶部（修 updated_at 冻结 bug）。"""
    a = store.get_or_create(None)
    store.add_message(a.id, "user", "会话A首条")
    b = store.get_or_create(None)
    store.add_message(b.id, "user", "会话B首条")  # 此刻 b 比 a 新
    store.add_message(a.id, "assistant", "A 的新活动")  # 非首条，仍要 bump
    ids = [r["id"] for r in client.get("/api/sessions").json()]
    assert ids.index(a.id) < ids.index(b.id)


def test_get_missing_session_404() -> None:
    r = client.get("/api/sessions/deadbeef")
    assert r.status_code == 404
    assert r.json()["code"] == "session_not_found"


def test_patch_missing_session_404() -> None:
    assert client.patch("/api/sessions/nope", json={"title": "x"}).status_code == 404


def test_delete_missing_session_404() -> None:
    assert client.delete("/api/sessions/nope").status_code == 404
