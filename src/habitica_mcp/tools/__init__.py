"""MCP tool definitions. Each module exposes ``register(mcp)``."""

from fastmcp import FastMCP

from . import adherence, checklist, overview, tags, tasks


def register_tools(mcp: FastMCP) -> None:
    overview.register(mcp)
    tasks.register(mcp)
    checklist.register(mcp)
    tags.register(mcp)
    adherence.register(mcp)
