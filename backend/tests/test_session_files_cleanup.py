"""M2/M3 验证：DELETE session 同步删 paper 文件夹；跨 session 隔离。

注意：用 `with TestClient(app) as client:` 触发 lifespan → init_saver 在
TestClient 的 event loop 跑（AsyncSqliteSaver 的 lock 绑 loop）。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.main import app
from app.session_store import store as global_store

# module-level 不创建 client；用 function-scoped fixture（lifespan 触发）

SYNTH = "x"
REVIEW = json.dumps({"covered_subquestions": ["q"], "citation_accuracy": "ok", "issues": [], "passed": True})


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kw):
        return self


@pytest.fixture
def client():
    """function-scoped TestClient 触发 lifespan，init_saver 跑在它的 event loop。"""
    with TestClient(app) as c:
        yield c


def _tc(name, args, i):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": i}])


SCRIPT = [
    _tc("search_arxiv", {"queries": ["q1"]}, "1"),
    _tc("download_papers", {}, "2"),
    _tc("write_review", {}, "3"),
    AIMessage(content="done"),
]

FAKE_PAPERS = [
    {"arxiv_id": "2401.00001", "title": "T1", "authors": ["A"], "year": 2024, "abstract": "x", "pdf_url": "http://x/1.pdf"},
]


async def _fake_download(client, sem, paper, dest_dir):
    # 写真实 PDF 到 session 子目录（与真 downloader 一致：arxid_id + ".pdf"）
    paper_file = dest_dir / f"{paper['arxiv_id'].replace('/', '_')}.pdf"
    paper_file.write_bytes(b"%PDF-fake")
    return {**paper, "local_path": str(paper_file)}, True


def _run_research_and_get_sid(query: str, session_id: str | None, c) -> str:
    body = {"query": query, "depth": "quick"}
    if session_id:
        body["session_id"] = session_id
    with (
        patch("app.agents.research_agent.get_llm", return_value=ScriptedModel(responses=SCRIPT)),
        patch("app.agents.nodes.searcher._search_one", return_value=FAKE_PAPERS),
        patch("app.agents.nodes.downloader._download_one", new=_fake_download),
        patch("app.agents.nodes.synthesizer.call_llm", new=AsyncMock(return_value=SYNTH)),
        patch("app.agents.nodes.reviewer.call_llm", new=AsyncMock(return_value=REVIEW)),
    ):
        r = c.post("/api/research", json=body)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_delete_session_removes_paper_folder(tmp_path, client) -> None:
    """M3: DELETE session 同步删 per-session paper 目录。"""
    from app.config import settings
    settings.paper_storage_dir = str(tmp_path)
    try:
        sid = _run_research_and_get_sid("方向A", session_id=None, c=client)
        paper_file = tmp_path / sid / "2401.00001.pdf"
        assert paper_file.exists()

        # 删 session
        r = client.delete(f"/api/sessions/{sid}")
        assert r.status_code == 204

        # 文件夹应被删
        assert not paper_file.exists()
        assert not (tmp_path / sid).exists()
    finally:
        # 还原（避免污染其他测试）
        settings.paper_storage_dir = "./data/papers"


def test_delete_session_does_not_affect_other_sessions(tmp_path, client) -> None:
    """M2: 删 session A 不应影响 session B 的 paper。"""
    from app.config import settings
    settings.paper_storage_dir = str(tmp_path)
    try:
        sid_a = _run_research_and_get_sid("方向A", session_id=None, c=client)
        sid_b = _run_research_and_get_sid("方向B", session_id=None, c=client)
        assert sid_a != sid_b

        # 两个 session 各自有文件
        assert (tmp_path / sid_a / "2401.00001.pdf").exists()
        assert (tmp_path / sid_b / "2401.00001.pdf").exists()

        # 删 A
        client.delete(f"/api/sessions/{sid_a}")
        assert not (tmp_path / sid_a).exists()
        # B 不受影响
        assert (tmp_path / sid_b / "2401.00001.pdf").exists()
    finally:
        settings.paper_storage_dir = "./data/papers"