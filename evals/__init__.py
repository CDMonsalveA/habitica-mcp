"""LLM evals for habitica-mcp.

Each case seeds the in-memory Habitica test double, lets a model work through the real MCP
server over stdio, then checks the final Habitica state and the tool calls. Drivers:

- ``claude_code``: Claude Code in headless mode (``claude -p``), using a Claude subscription
  (``CLAUDE_CODE_OAUTH_TOKEN`` from ``claude setup-token``) or an API key.
- ``ollama``: any local Ollama model with tool calling, at no API cost.

Run ``uv run python -m evals --help``. See docs/measurement.md.
"""
