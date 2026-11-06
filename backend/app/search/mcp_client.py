"""Small MCP client for the external ``paper-search-mcp`` server.

The server is launched as a child process for each operation.  This keeps the
application independent from provider SDKs and makes the MCP boundary explicit
while still allowing the existing research graph to persist its own state.
"""
from __future__ import annotations

import asyncio
import json
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import settings


class PaperSearchMCPError(RuntimeError):
    """Raised when the paper-search MCP call cannot produce a result."""


def _tool_payload(result: Any) -> Any:
    """Extract structured MCP output, with a text fallback for older servers."""
    if getattr(result, "isError", False):
        raise PaperSearchMCPError("paper-search-mcp returned an error")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, Mapping):
        payload = structured.get("result", structured)
        if payload is not None:
            return payload
    for item in getattr(result, "content", ()) or ():
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    raise PaperSearchMCPError("paper-search-mcp returned an empty result")


class PaperSearchMCP:
    """Invoke the two paper-search MCP tools used by the research workflow."""

    def __init__(self) -> None:
        command = settings.paper_search_mcp_command.strip()
        if not command:
            raise PaperSearchMCPError("PAPER_SEARCH_MCP_COMMAND is empty")
        self._command = command
        self._args = shlex.split(settings.paper_search_mcp_args)
        self._timeout = settings.paper_search_mcp_timeout_seconds

    def _server(self) -> StdioServerParameters:
        return StdioServerParameters(
            command=self._command,
            args=self._args,
            cwd=str(Path.cwd()),
        )

    async def _call(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            async with stdio_client(self._server()) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await asyncio.wait_for(
                        session.call_tool(name, arguments), timeout=self._timeout
                    )
                    return _tool_payload(result)
        except PaperSearchMCPError:
            raise
        except asyncio.TimeoutError as exc:
            raise PaperSearchMCPError(
                f"paper-search-mcp {name} timed out after {self._timeout:g}s"
            ) from exc
        except Exception as exc:
            raise PaperSearchMCPError(f"paper-search-mcp {name} failed: {exc}") from exc

    async def search(
        self,
        query: str,
        *,
        max_results_per_source: int,
        year: str = "",
    ) -> dict[str, Any]:
        payload = await self._call(
            "search_papers",
            {
                "query": query,
                "max_results_per_source": max_results_per_source,
                "sources": "all",
                "year": year,
            },
        )
        if not isinstance(payload, Mapping):
            raise PaperSearchMCPError("search_papers returned a non-object result")
        return dict(payload)

    async def download(
        self,
        *,
        source: str,
        paper_id: str,
        doi: str = "",
        title: str = "",
        save_path: str,
    ) -> str:
        payload = await self._call(
            "download_with_fallback",
            {
                "source": source,
                "paper_id": paper_id,
                "doi": doi,
                "title": title,
                "save_path": save_path,
                "use_scihub": False,
            },
        )
        if not isinstance(payload, str):
            raise PaperSearchMCPError("download_with_fallback returned a non-string result")
        return payload
