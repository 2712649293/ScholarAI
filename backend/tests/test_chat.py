"""问答 endpoint 冒烟测试。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_qa_returns_echo() -> None:
    resp = client.post("/api/chat/qa", json={"query": "你好"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "你问的是：你好"
    assert data["echo"] is True


def test_qa_missing_query_returns_422() -> None:
    resp = client.post("/api/chat/qa", json={})
    assert resp.status_code == 422


def test_qa_empty_query_returns_422() -> None:
    resp = client.post("/api/chat/qa", json={"query": ""})
    assert resp.status_code == 422


def test_qa_too_long_query_returns_422() -> None:
    resp = client.post("/api/chat/qa", json={"query": "a" * 2001})
    assert resp.status_code == 422
