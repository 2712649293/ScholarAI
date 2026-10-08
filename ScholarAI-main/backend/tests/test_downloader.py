"""M4.1 Downloader 节点测试（mock 网络）。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from app.agents.nodes import downloader

PAPERS = [
    {"arxiv_id": "2401.00001", "title": "T1", "pdf_url": "http://x/1.pdf"},
    {"arxiv_id": "2401.00002", "title": "T2", "pdf_url": "http://x/2.pdf"},
]


def test_download_success_and_failure(tmp_path) -> None:
    async def fake_fetch(client, url):
        if url.endswith("1.pdf"):
            return b"%PDF-1.4 fake"
        raise RuntimeError("boom")  # 非可重试错误 → 立即失败，不阻断

    with (
        patch.object(downloader.settings, "paper_storage_dir", str(tmp_path)),
        patch("app.agents.nodes.downloader._fetch", new=fake_fetch),
    ):
        out = asyncio.run(downloader.run({"papers": PAPERS, "session_id": "sessA"}))

    assert out["download_failures"] == ["2401.00002"]
    ok = next(p for p in out["papers"] if p["arxiv_id"] == "2401.00001")
    assert Path(ok["local_path"]).read_bytes() == b"%PDF-1.4 fake"
    # M2: 文件在 session 子目录下
    assert Path(ok["local_path"]).parent == tmp_path / "sessA"
    bad = next(p for p in out["papers"] if p["arxiv_id"] == "2401.00002")
    assert "local_path" not in bad


def test_download_skips_existing(tmp_path) -> None:
    # M2: 已存在检查在 session 子目录内
    (tmp_path / "sessA").mkdir()
    (tmp_path / "sessA" / "2401.00001.pdf").write_bytes(b"%PDF-old")
    calls = {"n": 0}

    async def fake_fetch(client, url):
        calls["n"] += 1
        return b"new"

    with (
        patch.object(downloader.settings, "paper_storage_dir", str(tmp_path)),
        patch("app.agents.nodes.downloader._fetch", new=fake_fetch),
    ):
        out = asyncio.run(downloader.run({"papers": [PAPERS[0]], "session_id": "sessA"}))

    assert calls["n"] == 0  # 已存在，跳过下载
    assert out["download_failures"] == []


def test_two_sessions_have_isolated_paper_dirs(tmp_path) -> None:
    """M2: session A 和 B 各下到自己的目录，互不干扰。"""
    async def fake_fetch(client, url):
        if "1.pdf" in url:
            return b"%PDF-A"
        return b"%PDF-B"

    with (
        patch.object(downloader.settings, "paper_storage_dir", str(tmp_path)),
        patch("app.agents.nodes.downloader._fetch", new=fake_fetch),
    ):
        asyncio.run(downloader.run({"papers": [PAPERS[0]], "session_id": "sessA"}))
        asyncio.run(downloader.run({"papers": [PAPERS[1]], "session_id": "sessB"}))

    assert (tmp_path / "sessA" / "2401.00001.pdf").read_bytes() == b"%PDF-A"
    assert (tmp_path / "sessB" / "2401.00002.pdf").read_bytes() == b"%PDF-B"
    # 互不污染
    assert not (tmp_path / "sessA" / "2401.00002.pdf").exists()
    assert not (tmp_path / "sessB" / "2401.00001.pdf").exists()


def test_download_total_timeout_protects_against_hung_request(tmp_path) -> None:
    """单个 hung 请求不能让 gather 永远挂住——总超时后全部失败（修 19 分钟卡死 bug）。"""
    import asyncio as _asyncio

    papers = [
        {"arxiv_id": f"hang-{i}", "title": f"T{i}", "pdf_url": f"http://x/{i}.pdf"}
        for i in range(3)
    ]

    async def hung_fetch(client, url):
        await _asyncio.sleep(60)  # 远超测试超时

    with (
        patch.object(downloader.settings, "paper_storage_dir", str(tmp_path)),
        patch("app.agents.nodes.downloader.DOWNLOAD_TOTAL_TIMEOUT", 0.5),
        patch("app.agents.nodes.downloader._fetch", new=hung_fetch),
    ):
        out = _asyncio.run(downloader.run({"papers": papers, "session_id": "sessA"}))

    assert sorted(out["download_failures"]) == ["hang-0", "hang-1", "hang-2"]
    assert out["papers"]  # 至少原 paper 结构保留


def test_download_empty() -> None:
    out = asyncio.run(downloader.run({"papers": []}))
    assert out == {"papers": [], "download_failures": []}
