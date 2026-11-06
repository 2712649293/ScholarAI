"""论文小模型配置与调用隔离；全部使用模拟客户端。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app import llm
from app.config import Settings


@pytest.fixture(autouse=True)
def isolated_llm(monkeypatch):
    config = Settings(_env_file=None)
    config.llm_model = "main-model"
    config.llm_base_url = "https://main.example/v1"
    config.llm_api_key = "main-key"
    config.llm_reasoning_effort = "high"
    config.small_llm_model = ""
    config.small_llm_base_url = ""
    config.small_llm_api_key = ""
    config.small_llm_reasoning_effort = ""
    monkeypatch.setattr(llm, "settings", config)
    llm.get_llm.cache_clear()
    llm.get_small_llm.cache_clear()
    yield config
    llm.get_llm.cache_clear()
    llm.get_small_llm.cache_clear()


def test_unconfigured_small_model_reuses_main(isolated_llm) -> None:
    with patch.object(llm, "ChatOpenAI") as client:
        assert llm.get_small_llm() is llm.get_llm()
    assert client.call_count == 1


def test_small_model_name_alone_reuses_api_but_not_reasoning(isolated_llm) -> None:
    isolated_llm.small_llm_model = "qwen-small"
    with patch.object(llm, "ChatOpenAI") as client:
        llm.get_small_llm()
    kwargs = client.call_args.kwargs
    assert kwargs["model"] == "qwen-small"
    assert kwargs["base_url"] == "https://main.example/v1"
    assert kwargs["api_key"].get_secret_value() == "main-key"
    assert kwargs["reasoning_effort"] == "none"


def test_independent_small_model_api(isolated_llm) -> None:
    isolated_llm.small_llm_model = "small-model"
    isolated_llm.small_llm_base_url = "https://small.example/v1"
    isolated_llm.small_llm_api_key = "small-key"
    isolated_llm.small_llm_reasoning_effort = "low"
    with patch.object(llm, "ChatOpenAI") as client:
        llm.get_small_llm()
        kwargs = client.call_args.kwargs
        assert kwargs["model"] == "small-model"
        assert kwargs["base_url"] == "https://small.example/v1"
        assert kwargs["api_key"].get_secret_value() == "small-key"
        assert kwargs["reasoning_effort"] == "low"
        llm.get_llm()
        assert client.call_args.kwargs["model"] == "main-model"


def test_new_endpoint_requires_explicit_key(isolated_llm) -> None:
    isolated_llm.small_llm_model = "small-model"
    isolated_llm.small_llm_base_url = "https://small.example/v1"
    with patch.object(llm, "ChatOpenAI") as client:
        with pytest.raises(ValueError, match="SMALL_LLM_API_KEY"):
            llm.get_small_llm()
    client.assert_not_called()


def test_call_llm_routes_only_explicit_small_model_requests() -> None:
    main = Mock(ainvoke=AsyncMock(return_value=Mock(text="main answer")))
    small = Mock(ainvoke=AsyncMock(return_value=Mock(text="small answer")))
    with (
        patch.object(llm, "get_llm", return_value=main),
        patch.object(llm, "get_small_llm", return_value=small),
    ):
        assert asyncio.run(llm.call_llm([])) == "main answer"
        assert asyncio.run(llm.call_llm([], use_small_model=True)) == "small answer"
    assert main.ainvoke.await_count == small.ainvoke.await_count == 1
