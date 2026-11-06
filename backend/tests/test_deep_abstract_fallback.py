"""Deep research uses full-text analysis first and abstracts after download failures."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.nodes import synthesizer, reviewer
from app.agents.tools import make_tools
from app.api.research import _finalize
from app.session_store import store
from tests.test_research_intent_flow import _runtime


def paper(identifier, **extra):
    return {"arxiv_id": identifier, "paper_key": f"arxiv:{identifier}",
            "title": f"Title {identifier}", "year": 2025, "abstract": f"abstract-{identifier}", **extra}


@pytest.mark.asyncio
@pytest.mark.parametrize("all_failed", [False, True])
async def test_deep_mixed_evidence_generation_review_and_finalization(all_failed):
    failed = paper("failed", download_status="failed")
    full = paper("full", local_path="/papers/full.pdf", download_status="downloaded")
    excluded = paper("excluded", download_status="failed")
    analyses = [{"paper_key": "arxiv:full", "status": "completed", "results": "full-text-results"}]
    state = {"query": "q", "research_mode": "deep", "papers": [failed, excluded] if all_failed else [full, failed, excluded],
             "analyses": [] if all_failed else analyses,
             "selected_paper_keys": ["arxiv:full", "arxiv:failed"], "download_failures": ["arxiv:failed", "arxiv:excluded"]}
    synthesis = AsyncMock(return_value="综述 [failed]")
    with patch.object(synthesizer, "call_llm", synthesis):
        result = await make_tools()[3].ainvoke({"runtime": _runtime(state)})
    state.update(result.update)
    prompt = synthesis.call_args.args[0][1].content
    assert "abstract-failed" in prompt and "仅依据摘要" in prompt
    assert "abstract-full" not in prompt
    assert "Title excluded" not in prompt
    assert ("full-text-results" in prompt) is (not all_failed)
    assert "证据说明" in state["draft"] and "Title failed" in state["draft"]

    review = AsyncMock(return_value='{"covered_subquestions": [], "citation_accuracy": "ok", "issues": [], "passed": true}')
    with patch.object(reviewer, "call_llm", review):
        result = await make_tools()[4].ainvoke({"runtime": _runtime(state)})
    state.update(result.update)
    review_prompt = review.call_args.args[0][1].content
    assert "abstract-failed" in review_prompt
    assert "Title excluded" not in review_prompt
    assert state["review_status"] == "passed"

    sid = store.get_or_create(None, mode="research").id
    agent = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values=state)))
    final = await _finalize(sid, agent, {})
    assert final[3] is None and final[5] == "done"
    state["review_status"] = "failed"
    final = await _finalize(sid, agent, {})
    assert final[3] == "review_failed" and final[5] == "needs_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("record,failures,analyses", [
    (paper("p"), [], []),  # download not attempted
    (paper("p", download_status="failed", abstract="  "), ["p"], []),
    (paper("p", local_path="/papers/p.pdf"), [], []),  # analysis not attempted
    (paper("p", local_path="/papers/p.pdf"), [], [{"arxiv_id": "p", "status": "failed"}]),
])
async def test_deep_no_evidence_cannot_bypass_download_or_analysis(record, failures, analyses):
    state = {"query": "q", "research_mode": "deep", "papers": [record],
             "selected_paper_keys": ["arxiv:p"], "download_failures": failures, "analyses": analyses}
    with patch.object(synthesizer, "call_llm", new_callable=AsyncMock) as llm:
        result = await make_tools()[3].ainvoke({"runtime": _runtime(state)})
    assert "draft" not in result.update
    llm.assert_not_called()


@pytest.mark.asyncio
async def test_timeout_failure_ids_enable_fallback_without_status_field():
    state = {"query": "q", "research_mode": "deep", "papers": [paper("timeout")],
             "selected_paper_keys": ["arxiv:timeout"], "download_failures": ["timeout"]}
    with patch.object(synthesizer, "call_llm", new=AsyncMock(return_value="综述")):
        result = await make_tools()[3].ainvoke({"runtime": _runtime(state)})
    assert "仅依据摘要" in result.update["draft"]


@pytest.mark.asyncio
async def test_all_downloads_failed_tool_guides_agent_to_write_then_review():
    state = {"research_mode": "deep", "papers": [paper("p")], "selected_paper_keys": ["arxiv:p"]}
    with patch("app.agents.tools.downloader.run", new=AsyncMock(return_value={
        "papers": [paper("p", download_status="failed")], "download_failures": ["p"],
    })):
        result = await make_tools()[1].ainvoke({"runtime": _runtime(state)})
    message = result.update["messages"][0].content
    assert "write_review" in message and "review_report" in message and "摘要" in message
