"""代码检查发现的问题：验证业务结果、持久化与边界条件。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pymupdf
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app.config import settings
from app.session_store import store
from tests.test_research_plan_api import _make_plan_json, _patch_planner_llm


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def make_pdf(pages=1):
    with pymupdf.open() as pdf:
        for _ in range(pages):
            page = pdf.new_page()
            page.insert_text((50, 50), "RAG retrieval text")
        return pdf.tobytes()


def test_initial_research_contains_user_question():
    from app.api.research import _initial_state
    from app.schemas.research import ResearchRequest
    state = _initial_state(ResearchRequest(query="研究方向"), "sid")
    assert state["messages"][0].content == "研究方向"


def test_clarification_rounds_are_saved_and_history_not_repeated(client):
    model = ScriptedModel(responses=[AIMessage(content="请确认年限"), AIMessage(content="请确认年限")])
    with patch("app.agents.clarify_graph.get_llm", return_value=model):
        first = client.post("/api/research/clarify/stream", json={"query": "研究RAG", "session_id": "clarify-review"})
        second = client.post("/api/research/clarify-review/clarify/continue", json={"query": "近五年"})
    assert first.status_code == second.status_code == 200
    assert second.text.count("event: message") == 1
    assert "event: complete" in second.text
    history = client.get("/api/sessions/clarify-review").json()["messages"]
    assert [m["content"] for m in history] == ["研究RAG", "请确认年限", "近五年", "请确认年限"]
    assert client.get("/api/research/clarify-review/plan").json()["plan_status"] == "clarifying"
    assert client.post("/api/research/missing/clarify/continue", json={"query": "q"}).status_code == 404


def test_followup_sse_history_and_report_match_and_delete_clears_checkpoint(client):
    from app.agents import research_agent
    sid = "review-followup"
    store.get_or_create(sid, mode="research")
    config = {"configurable": {"thread_id": sid}}
    model = ScriptedModel(responses=[AIMessage(content="旧回复"), AIMessage(content="本轮新回复")])
    with patch("app.agents.research_agent.get_llm", return_value=model):
        agent = research_agent.build_agent()
        client.portal.call(agent.ainvoke, {
            "query": "旧方向", "draft": "旧综述", "plan_status": "approved",
            "session_id": sid, "papers": [{"arxiv_id": "2401.1", "title": "t"}],
            "messages": [HumanMessage(content="旧方向")],
        }, config)
        response = client.post(f"/api/research/{sid}/continue/stream", json={"query": "新追问"})
    assert "本轮新回复" in response.text
    import json
    final = next(json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data:"))
    assert Path(final["report_path"]).read_text() == "本轮新回复"
    history = client.get(f"/api/sessions/{sid}").json()["messages"]
    assert [m["content"] for m in history] == ["新追问", "本轮新回复"]
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert not Path(final["report_path"]).exists()
    assert client.get(f"/api/research/{sid}/plan").json()["plan_status"] == "none"


def test_plan_validates_edits_and_cannot_reapprove(client):
    with _patch_planner_llm():
        sid = client.post("/api/research/plan", json={"query": "q"}).json()["session_id"]
    assert client.patch(f"/api/research/{sid}/plan", json={"plan": {"outline": []}}).status_code == 422
    assert client.post(f"/api/research/{sid}/plan/approve", json={"edited_plan": {"title": "bad"}}).status_code == 422
    assert client.get(f"/api/research/{sid}/plan").json()["plan_status"] == "pending"
    with patch("app.agents.research_agent.get_llm", return_value=ScriptedModel(responses=[AIMessage(content="done")])):
        assert client.post(f"/api/research/{sid}/plan/approve").status_code == 200
    assert client.post(f"/api/research/{sid}/plan/approve").status_code == 409
    assert client.post(f"/api/research/{sid}/plan/reject").status_code == 409
    assert client.patch(f"/api/research/{sid}/plan", json={"plan": {"title": "x"}}).status_code == 409


@pytest.mark.parametrize("body", [{"query": ""}, {"query": "q", "max_papers": -1}])
def test_plan_rejects_invalid_input(client, body):
    assert client.post("/api/research/plan", json=body).status_code == 422


def test_session_id_cannot_escape_storage(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "paper_storage_dir", str(tmp_path / "papers"))
    outside = tmp_path / "keep"
    outside.mkdir()
    marker = outside / "file"
    marker.write_text("keep")
    assert client.post("/api/chat/qa", json={"query": "q", "session_id": "../keep"}).status_code == 400
    store.delete_files("../keep")
    assert marker.read_text() == "keep"


def test_upload_limits_cleanup_and_delete_pdf(client, tmp_path, monkeypatch, fake_embeddings):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    kid = client.post("/api/knowledge", json={"name": "review-kb"}).json()["id"]
    monkeypatch.setattr(settings, "upload_max_size_mb", 1)
    response = client.post(f"/api/knowledge/{kid}/docs", files={"file": ("big.pdf", b"%PDF-" + b"a" * (1024 * 1024))})
    assert response.status_code == 413
    assert list(tmp_path.glob("*.pdf")) == []
    monkeypatch.setattr(settings, "upload_max_pages", 1)
    response = client.post(f"/api/knowledge/{kid}/docs", files={"file": ("two.pdf", make_pdf(2))})
    assert response.status_code == 201
    doc = client.get(f"/api/knowledge/{kid}/docs/{response.json()['id']}").json()
    assert doc["status"] == "failed" and "页上限" in doc["error"]
    assert client.get(f"/api/knowledge/{kid}").json()["vector_count"] == 0
    assert list(tmp_path.glob("*.pdf"))
    assert client.delete(f"/api/knowledge/{kid}").status_code == 204
    assert list(tmp_path.glob("*.pdf")) == []


@pytest.mark.asyncio
async def test_year_filter_and_partial_empty_search():
    from app.agents.nodes import searcher
    hit = lambda year: {"year": year, "arxiv_id": str(year)}
    mocked = AsyncMock(return_value=[hit(2015), hit(2022), hit(2025)])
    with patch.object(searcher, "_search_with_timeout", mocked):
        result = await searcher.run({"search_queries": ["rag"], "clarify_direction": {"year_start": 2010, "year_end": 2023}})
    assert [p["year"] for p in result["papers"]] == [2022, 2015]
    assert "submittedDate:[201001010000 TO 202312312359]" in mocked.call_args.args[0]
    with patch.object(searcher, "_search_with_timeout", AsyncMock(side_effect=[RuntimeError(), []])):
        assert (await searcher.run({"search_queries": ["a", "b"]}))["papers"] == []


@pytest.mark.asyncio
async def test_review_feedback_and_approved_outline_reach_synthesizer():
    from app.agents.tools import make_tools
    from app.agents.nodes import synthesizer
    from tests.test_tools import _runtime
    runtime = _runtime()
    runtime.state = {"query": "q", "papers": [{"arxiv_id": "p", "year": 2024, "title": "t", "abstract": "abs"}], "draft": "旧综述", "feedback": "补充局限", "plan": _make_plan_json()}
    mocked = AsyncMock(return_value="修订综述")
    with patch.object(synthesizer, "call_llm", mocked):
        result = await make_tools()[3].ainvoke({"runtime": runtime})
    prompt = mocked.call_args.args[0][1].content
    assert "补充局限" in prompt and "旧综述" in prompt and "LLM 推理综述" in prompt
    assert result.update["feedback"] == ""


@pytest.mark.asyncio
async def test_reviewer_reads_actual_report_body():
    from app.agents.tools import make_tools
    from app.agents.nodes import reviewer
    from tests.test_tools import _runtime
    import json
    runtime = _runtime()
    runtime.state = {"papers": [{"arxiv_id": "p"}], "draft": "应当审校的完整正文"}
    mocked = AsyncMock(return_value=json.dumps({"covered_subquestions": [], "citation_accuracy": "ok", "issues": [], "passed": True}))
    with patch.object(reviewer, "call_llm", mocked):
        await make_tools()[4].ainvoke({"runtime": runtime})
    assert "应当审校的完整正文" in mocked.call_args.args[0][1].content


def test_pdf_fallback_does_not_duplicate_partial_pages(tmp_path):
    from app.rag.pdf_loader import load_pdf
    path = tmp_path / "fallback.pdf"
    path.write_bytes(make_pdf(2))
    class BrokenPDF:
        def __enter__(self):
            self.pages = [SimpleNamespace(extract_text=lambda: "partial"), SimpleNamespace(extract_text=lambda: (_ for _ in ()).throw(RuntimeError("broken")))]
            return self
        def __exit__(self, *args):
            pass
    with patch("app.rag.pdf_loader.pdfplumber.open", return_value=BrokenPDF()):
        pages = load_pdf(path)
    assert len(pages) == 2 and [p.page for p in pages] == [1, 2]
