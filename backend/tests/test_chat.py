"""问答 endpoint 冒烟测试（M1.4：mock LLM）。"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.session_store import store

client = TestClient(app)


def _setup_clean_store() -> None:
    """每个测试前清空内存 store。"""
    store._sessions.clear()  # type: ignore[attr-defined]


def test_qa_returns_llm_reply() -> None:
    _setup_clean_store()
    with patch("app.api.chat.call_llm", new=AsyncMock(return_value="LLM 模拟回答")):
        resp = client.post("/api/chat/qa", json={"query": "你好"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "LLM 模拟回答"
    assert data["echo"] is False
    assert "session_id" in data and len(data["session_id"]) > 0


def test_qa_missing_query_returns_422() -> None:
    resp = client.post("/api/chat/qa", json={})
    assert resp.status_code == 422


def test_qa_empty_query_returns_422() -> None:
    resp = client.post("/api/chat/qa", json={"query": ""})
    assert resp.status_code == 422


def test_qa_reuses_session_id_for_history() -> None:
    _setup_clean_store()
    with patch("app.api.chat.call_llm", new=AsyncMock(side_effect=["R1", "R2"])):
        r1 = client.post("/api/chat/qa", json={"query": "Q1"})
        sid = r1.json()["session_id"]
        r2 = client.post("/api/chat/qa", json={"query": "Q2", "session_id": sid})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["reply"] == "R1"
    assert r2.json()["reply"] == "R2"
    # store 里应有 4 条消息
    assert len(store.get(sid).messages) == 4  # type: ignore[union-attr]


def test_qa_llm_timeout_returns_504() -> None:
    from app.errors import LLMTimeout
    _setup_clean_store()
    with patch("app.api.chat.call_llm", new=AsyncMock(side_effect=LLMTimeout("oops"))):
        resp = client.post("/api/chat/qa", json={"query": "x"})
    assert resp.status_code == 504
    body = resp.json()
    assert body["code"] == "llm_timeout"
    assert "request_id" in body


def test_response_includes_request_id_header() -> None:
    _setup_clean_store()
    with patch("app.api.chat.call_llm", new=AsyncMock(return_value="ok")):
        resp = client.post(
            "/api/chat/qa",
            json={"query": "x"},
            headers={"X-Request-ID": "test-rid-123"},
        )
    assert resp.headers.get("X-Request-ID") == "test-rid-123"
