"""问答 endpoint 冒烟测试（M1.4：mock LLM）。"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.session_store import store

client = TestClient(app)


def _setup_clean_store() -> None:
    """DB 版 store 每个测试自建新 session，无需清空（且不能清——测试跑在真库上）。"""
    pass


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
    assert len(store.recent(sid, n=20)) == 4


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


def test_qa_with_kb_returns_citations() -> None:
    """挂载 KB 时：返回 citations，且 LLM 收到的 prompt 含参考资料块。"""
    _setup_clean_store()
    fake_hits = [
        {
            "text": "RAG 检索增强生成提升事实准确性。",
            "doc_id": "doc-abc",
            "page": 3,
            "chunk_index": 0,
            "score": 0.92,
        },
        {
            "text": "LangGraph 是 LangChain 的扩展，支持循环与状态。",
            "doc_id": "doc-def",
            "page": 1,
            "chunk_index": 2,
            "score": 0.81,
        },
    ]
    captured: dict = {}

    async def fake_call_llm(messages, **kw):
        # 抓住 prompt 内容用于断言
        captured["sys"] = messages[0].content
        captured["user"] = messages[-1].content
        return "RAG 是一种 [1]，而 LangGraph [2]..."

    with (
        patch("app.api.chat.call_llm", new=AsyncMock(side_effect=fake_call_llm)),
        patch("app.api.chat.indexer.search", return_value=fake_hits),
    ):
        resp = client.post(
            "/api/chat/qa",
            json={"query": "解释 RAG 和 LangGraph", "kb_ids": ["kb-1"]},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["citations"]) == 2
    assert body["citations"][0]["doc_id"] == "doc-abc"
    assert body["citations"][0]["score"] == 0.92
    # LLM 收到的 prompt 应包含参考资料块
    assert "参考资料" in captured["sys"]
    assert "[1]" in captured["sys"]
    assert "doc-abc" in captured["sys"]
    assert "LangGraph" in captured["sys"]


def test_qa_without_kb_has_no_citations() -> None:
    _setup_clean_store()
    with patch("app.api.chat.call_llm", new=AsyncMock(return_value="通用回答")):
        resp = client.post("/api/chat/qa", json={"query": "你好"})
    assert resp.status_code == 200
    assert resp.json()["citations"] == []


def test_qa_rag_search_failure_degrades_gracefully() -> None:
    """单 KB 检索失败不阻断整体流程（§8.3 降级）。"""
    _setup_clean_store()
    with (
        patch("app.api.chat.call_llm", new=AsyncMock(return_value="回答")),
        patch("app.api.chat.indexer.search", side_effect=RuntimeError("chroma down")),
    ):
        resp = client.post("/api/chat/qa", json={"query": "x", "kb_ids": ["kb-bad"]})
    assert resp.status_code == 200
    assert resp.json()["citations"] == []
