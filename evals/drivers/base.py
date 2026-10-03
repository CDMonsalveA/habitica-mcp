"""What every driver receives and returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

SERVER_PREFIX = "mcp__habitica__"


@dataclass(frozen=True)
class ServerSpec:
    """How to start the MCP server under test (stdio)."""

    command: str
    args: list[str]
    env: dict[str, str]
    cwd: str


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    is_error: bool = False
    result_chars: int = 0


@dataclass
class Trace:
    """Everything a run produced that scoring and reports need."""

    final_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    other_tool_calls: int = 0
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    turns: int | None = None
    duration_s: float = 0.0
    error: str | None = None


class Driver(Protocol):
    name: str
    model: str

    async def run(self, prompt: str, *, server: ServerSpec, system_context: str) -> Trace: ...


def strip_server_prefix(name: str) -> str | None:
    """Claude Code names MCP tools ``mcp__<server>__<tool>``; return ``<tool>`` for ours."""
    return name[len(SERVER_PREFIX) :] if name.startswith(SERVER_PREFIX) else None
