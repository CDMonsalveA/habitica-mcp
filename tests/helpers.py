"""Shared test helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastmcp import Client
from mcp_types import TextContent

# Wednesday 2026-09-16, 09:00 in the fake user's UTC-5 time zone.
FIXED_NOW = datetime(2026, 9, 16, 14, 0, tzinfo=UTC)
UTC_MINUS_5 = 300


async def call(client: Client[Any], tool: str, **arguments: Any) -> dict[str, Any]:
    """Call a tool that must succeed and return its structured content."""
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    return result.structured_content


async def call_error(client: Client[Any], tool: str, **arguments: Any) -> str:
    """Call a tool that must fail and return the error text shown to the model."""
    result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error, result.structured_content
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text
