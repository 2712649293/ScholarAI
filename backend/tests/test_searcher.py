"""M5.5.9 · searcher 单 query timeout 测试。

mock `_search_one` 验证 searcher 的容错行为（不真跑 arxiv API）。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.agents.nodes import searcher
from app.errors import ArxivFetchFailed


def _make_hit(idx: int) -> dict:
    return {
        "arxiv_id": f"2401.{idx:05d}v1",
        "title": f"Test Paper {idx}",
        "authors": ["Author A"],
        "year": 2024,
        "abstract": "abstract",
        "pdf_url": f"https://arxiv.org/pdf/2401.{idx:05d}",
    }


def test_searcher_returns_papers_on_success():
    """全部 query 成功 → 返回 papers（按年份降序）。"""

    def fake_search(q: str, per: int):
        # 每个 query 返 3 篇
        return [_make_hit(i) for i in range(int(q.split("idx=")[-1]) * 10, int(q.split("idx=")[-1]) * 10 + 3)]

    async def main():
        with patch.object(searcher, "_search_one", side_effect=fake_search):
            result = await searcher.run({
                "search_queries": ["q1 idx=1", "q2 idx=2"],
                "depth": "normal",
                "max_papers": 20,
            })
        assert len(result["papers"]) == 6  # 2 个 query × 3 篇
        # 按 year 降序；fake 全 2024，顺序由 dict 决定

    asyncio.run(main())


def test_searcher_timeout_marks_query_failed():
    """部分 query 超时 → 视为失败但继续，返剩下的 papers（不抛错）。"""

    call_count = {"n": 0}

    def fake_search(q: str, per: int):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 第一次 timeout（searcher 会捕获）
            raise asyncio.TimeoutError()
        return [_make_hit(call_count["n"])]

    async def slow_search(*args, **kwargs):
        # 让 asyncio.to_thread 内部真超时——通过 wait_for
        # 实际上 patch._search_one 会替换原函数，但 to_thread 跑的是同步函数。
        # 这里改用 patch._search_with_timeout 直接模拟 timeout
        raise asyncio.TimeoutError()

    async def main():
        # patch _search_with_timeout 让它返 timeout（模拟真实行为）
        async def fake_with_timeout(q, per):
            raise asyncio.TimeoutError()

        with patch.object(searcher, "_search_with_timeout", side_effect=fake_with_timeout):
            # 全部 timeout 但又有成功分支（patch side_effect 是单一 raise，无返回），
            # 改用 side_effect 列表控制部分 timeout
            pass

        # 重做：第一次 timeout，第二次返 papers
        counter = {"n": 0}

        async def mixed(q, per):
            counter["n"] += 1
            if counter["n"] == 1:
                raise asyncio.TimeoutError()
            return [_make_hit(counter["n"])]

        with patch.object(searcher, "_search_with_timeout", side_effect=mixed):
            result = await searcher.run({
                "search_queries": ["q1", "q2"],
                "depth": "normal",
                "max_papers": 20,
            })
        # 第一 query timeout，第二 query 成功 → 1 篇 paper，不抛错
        assert len(result["papers"]) == 1
        assert result["papers"][0]["arxiv_id"] == "2401.00002v1"

    asyncio.run(main())


def test_searcher_all_timeout_raises_fetch_failed():
    """所有 query 都 timeout → 抛 ArxivFetchFailed（被 tools.py 翻译成 ToolMessage）。"""

    async def fake_with_timeout(q, per):
        raise asyncio.TimeoutError()

    async def main():
        with patch.object(searcher, "_search_with_timeout", side_effect=fake_with_timeout):
            with pytest.raises(ArxivFetchFailed) as exc_info:
                await searcher.run({
                    "search_queries": ["q1", "q2", "q3"],
                    "depth": "normal",
                    "max_papers": 20,
                })
        # 错误 message 应含 "arxiv 检索全部失败"
        assert "arxiv 检索全部失败" in str(exc_info.value.message)
        assert exc_info.value.details.get("reason") == "all_timeout_or_error"

    asyncio.run(main())


def test_searcher_query_timeout_real_wait():
    """真实 wait_for 行为：单 query 超过 QUERY_TIMEOUT 立即抛 TimeoutError。"""
    import time

    async def fast_main():
        # 临时改 QUERY_TIMEOUT 为 0.3s 验证 wait_for 工作
        original = searcher.QUERY_TIMEOUT
        searcher.QUERY_TIMEOUT = 0.3
        try:
            def sleep_search(q, per):
                time.sleep(2)  # 模拟 SDK hang
                return []
            with patch.object(searcher, "_search_one", side_effect=sleep_search):
                t0 = time.time()
                with pytest.raises(asyncio.TimeoutError):
                    await searcher._search_with_timeout("q", 10)
                elapsed = time.time() - t0
                # 0.3s timeout 触发；不会等满 2s sleep
                assert elapsed < 1.5, f"wait_for 没生效，elapsed={elapsed}"
        finally:
            searcher.QUERY_TIMEOUT = original

    asyncio.run(fast_main())