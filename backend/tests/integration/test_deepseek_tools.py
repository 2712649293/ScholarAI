"""M4.5.0 smoke test：验证 deepseek-v4-flash 的 function calling 能力。

需真实网络 + key，默认 pytest 跳过（标 integration）。
直接跑：python -m tests.integration.test_deepseek_tools
"""
from __future__ import annotations

import pytest
from langchain_core.tools import tool

from app.llm import get_llm


@tool
def search_arxiv(queries: list[str]) -> str:
    """按关键词检索 arxiv 论文。queries 是英文检索短语列表。"""
    return f"找到 {len(queries)} 组检索词的论文"


@tool
def get_weather(city: str) -> str:
    """查询某城市天气。"""
    return f"{city} 晴"


def _check_tool_calling() -> dict:
    llm = get_llm().bind_tools([search_arxiv, get_weather])

    # 1) 单工具选择 + 填参
    r1 = llm.invoke("帮我研究一下 LLM 推理加速这个方向，先去检索相关论文")
    picked = [tc["name"] for tc in r1.tool_calls]
    args_ok = any(
        tc["name"] == "search_arxiv" and tc["args"].get("queries")
        for tc in r1.tool_calls
    )

    # 2) 干扰项：明显该选 weather 而非 search
    r2 = llm.invoke("北京今天天气怎么样？")
    picked2 = [tc["name"] for tc in r2.tool_calls]

    return {
        "call1_tools": picked,
        "call1_args_ok": args_ok,
        "call2_tools": picked2,
        "supports": bool(picked) and args_ok and picked2 == ["get_weather"],
    }


@pytest.mark.integration
def test_deepseek_supports_tool_calling() -> None:
    result = _check_tool_calling()
    assert result["supports"], f"deepseek 工具调用不达标：{result}"


if __name__ == "__main__":
    import json

    print(json.dumps(_check_tool_calling(), ensure_ascii=False, indent=2))
