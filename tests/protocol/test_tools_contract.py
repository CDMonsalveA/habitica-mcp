"""The tool list is this server's public API: names, descriptions and schemas are what
models learn to call. Any change must be deliberate, so it is compared to a reviewed
snapshot. Regenerate it with ``UPDATE_CONTRACT=1 uv run pytest tests/protocol``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from fastmcp import Client

from habitica_mcp.config import Settings
from habitica_mcp.server import create_server

CONTRACT = Path(__file__).with_name("tools_contract.json")
# Tool descriptions and input schemas are sent to the model with every request.
MAX_MODEL_FACING_CHARS = 16_000


async def current_contract() -> dict[str, Any]:
    async with Client(create_server(Settings())) as client:
        tools = await client.list_tools()
    contract: dict[str, Any] = {}
    for tool in tools:
        output = tool.output_schema or {}
        contract[tool.name] = {
            "title": tool.title,
            "description": tool.description,
            "annotations": tool.annotations.model_dump(exclude_none=True)
            if tool.annotations
            else None,
            "tags": (tool.meta or {}).get("fastmcp", {}).get("tags"),
            "input_schema": tool.input_schema,
            "output_properties": sorted(output.get("properties", {})),
            "output_schema_sha256": hashlib.sha256(
                json.dumps(output, sort_keys=True).encode()
            ).hexdigest(),
        }
    return contract


async def test_tools_match_the_reviewed_contract() -> None:
    contract = await current_contract()
    rendered = json.dumps(contract, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_CONTRACT") == "1" or not CONTRACT.exists():
        CONTRACT.write_text(rendered, encoding="utf-8")
    assert json.loads(CONTRACT.read_text(encoding="utf-8")) == contract, (
        "The MCP tool contract changed. If this is intended, run "
        "`UPDATE_CONTRACT=1 uv run pytest tests/protocol`, review the diff and note it in "
        "CHANGELOG.md."
    )


async def test_model_facing_definitions_stay_within_budget() -> None:
    contract = await current_contract()
    size = sum(
        len(tool["description"] or "") + len(json.dumps(tool["input_schema"]))
        for tool in contract.values()
    )
    assert size <= MAX_MODEL_FACING_CHARS, f"tool definitions grew to {size} characters"
