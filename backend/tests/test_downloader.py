"""Downloader tests at the paper-search-mcp boundary."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from app.agents.nodes import downloader

PAPERS = [
    {"arxiv_id": "2401.00001", "title": "T1", "sources": ["arxiv"], "source_ids": {"arxiv": "2401.00001"}},
    {"arxiv_id": "2401.00002", "title": "T2", "sources": ["arxiv"], "source_ids": {"arxiv": "2401.00002"}},
]


class FakeMCP:
    def __init__(self, *, hang: bool = False):
        self.hang = hang
        self.calls: list[str] = []

    async def download(self, *, source, paper_id, doi, title, save_path):
        self.calls.append(paper_id)
        if self.hang:
            await asyncio.sleep(60)
        if paper_id.endswith("00002"):
            raise downloader.PaperSearchMCPError("offline")
        path = Path(save_path) / f"mcp-{paper_id}.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        return str(path)


def test_download_success_and_failure(tmp_path) -> None:
    fake = FakeMCP()
    with patch.object(downloader.settings, "paper_storage_dir", str(tmp_path)), patch(
        "app.agents.nodes.downloader.PaperSearchMCP", return_value=fake
    ):
        out = asyncio.run(downloader.run({"papers": PAPERS, "session_id": "sessA"}))

    assert out["download_failures"] == ["2401.00002"]
    ok = next(p for p in out["papers"] if p["arxiv_id"] == "2401.00001")
    assert Path(ok["local_path"]).read_bytes() == b"%PDF-1.4 fake"
    assert Path(ok["local_path"]).parent == tmp_path / "sessA"


def test_download_skips_existing(tmp_path) -> None:
    path = tmp_path / "sessA" / "2401.00001.pdf"
    path.parent.mkdir()
    path.write_bytes(b"%PDF-old")
    fake = FakeMCP()
    with patch.object(downloader.settings, "paper_storage_dir", str(tmp_path)), patch(
        "app.agents.nodes.downloader.PaperSearchMCP", return_value=fake
    ):
        out = asyncio.run(downloader.run({"papers": [PAPERS[0]], "session_id": "sessA"}))
    assert fake.calls == []
    assert out["download_failures"] == []


def test_two_sessions_have_isolated_paper_dirs(tmp_path) -> None:
    with patch.object(downloader.settings, "paper_storage_dir", str(tmp_path)), patch(
        "app.agents.nodes.downloader.PaperSearchMCP", side_effect=lambda: FakeMCP()
    ):
        asyncio.run(downloader.run({"papers": [PAPERS[0]], "session_id": "sessA"}))
        asyncio.run(downloader.run({"papers": [PAPERS[0]], "session_id": "sessB"}))
    assert (tmp_path / "sessA" / "2401.00001.pdf").exists()
    assert (tmp_path / "sessB" / "2401.00001.pdf").exists()


def test_download_total_timeout_protects_against_hung_mcp(tmp_path) -> None:
    fake = FakeMCP(hang=True)
    with patch.object(downloader.settings, "paper_storage_dir", str(tmp_path)), patch(
        "app.agents.nodes.downloader.DOWNLOAD_TOTAL_TIMEOUT", 0.05
    ), patch("app.agents.nodes.downloader.PaperSearchMCP", return_value=fake):
        out = asyncio.run(downloader.run({"papers": PAPERS[:1], "session_id": "sessA"}))
    assert out["download_failures"] == ["2401.00001"]


def test_download_empty() -> None:
    assert asyncio.run(downloader.run({"papers": []})) == {"papers": [], "download_failures": []}


def test_download_only_selected_papers(tmp_path) -> None:
    fake = FakeMCP()
    with patch.object(downloader.settings, "paper_storage_dir", str(tmp_path)), patch(
        "app.agents.nodes.downloader.PaperSearchMCP", return_value=fake
    ):
        out = asyncio.run(
            downloader.run(
                {"papers": PAPERS, "session_id": "sess-selected", "selected_paper_keys": ["arxiv:2401.00001"]}
            )
        )
    assert fake.calls == ["2401.00001"]
    selected = next(p for p in out["papers"] if p["arxiv_id"] == "2401.00001")
    skipped = next(p for p in out["papers"] if p["arxiv_id"] == "2401.00002")
    assert selected["download_status"] == "downloaded"
    assert skipped["download_status"] == "not_selected"
