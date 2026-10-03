"""Drive Claude Code in headless mode (``claude -p``) against the MCP server.

Authentication, in Claude Code's own order: ``ANTHROPIC_API_KEY`` (API billing) or
``CLAUDE_CODE_OAUTH_TOKEN`` (a Pro/Max/Team subscription token from ``claude setup-token``).
When one of those is set the run uses an empty ``CLAUDE_CONFIG_DIR`` so personal hooks,
plugins, memory and CLAUDE.md files cannot influence results. Without them, the logged-in
account is used with the default configuration (the report records that).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .base import ServerSpec, ToolCall, Trace, strip_server_prefix

# Built-in tools are removed so the model can only act through the MCP server.
BUILTIN_TOOLS = (
    "Bash,Edit,Write,Read,Glob,Grep,NotebookEdit,WebFetch,WebSearch,Task,Agent,TodoWrite,Skill"
)
DEFAULT_TIMEOUT_SECONDS = 300.0


class ClaudeCodeDriver:
    name = "claude_code"

    def __init__(
        self,
        model: str,
        *,
        max_turns: int = 12,
        claude_bin: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.model = model
        self.max_turns = max_turns
        self.claude_bin = claude_bin or shutil.which("claude")
        self.timeout_seconds = timeout_seconds
        self.isolated = bool(
            os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
        )

    def command(self, prompt: str, mcp_config: Path, system_context: str) -> list[str]:
        if not self.claude_bin:
            raise RuntimeError(
                "Claude Code CLI not found. Install it (https://code.claude.com) and run "
                "`claude setup-token`, then export CLAUDE_CODE_OAUTH_TOKEN."
            )
        prefix = ["cmd", "/c"] if self.claude_bin.lower().endswith((".cmd", ".bat")) else []
        return [
            *prefix,
            self.claude_bin,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            self.model,
            "--max-turns",
            str(self.max_turns),
            "--no-session-persistence",
            "--strict-mcp-config",
            "--mcp-config",
            str(mcp_config),
            "--allowedTools",
            "mcp__habitica__*",
            "--disallowedTools",
            BUILTIN_TOOLS,
            "--permission-prompts",
            "none",
            "--append-system-prompt",
            system_context,
        ]

    async def run(self, prompt: str, *, server: ServerSpec, system_context: str) -> Trace:
        with tempfile.TemporaryDirectory(prefix="habitica-eval-") as workdir:
            config_path = Path(workdir) / "mcp.json"
            config_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "habitica": {
                                "type": "stdio",
                                "command": server.command,
                                "args": server.args,
                                "env": server.env,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = dict(os.environ)
            if self.isolated:
                env["CLAUDE_CONFIG_DIR"] = str(Path(workdir) / "claude-config")
            started = time.perf_counter()
            process = await asyncio.create_subprocess_exec(
                *self.command(prompt, config_path, system_context),
                cwd=workdir,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout_seconds
                )
            except TimeoutError:
                process.kill()
                return Trace(error="timeout", duration_s=time.perf_counter() - started)
            trace = parse_stream_json(stdout.decode("utf-8", errors="replace"))
            trace.duration_s = time.perf_counter() - started
            if process.returncode not in (0, None) and trace.error is None and not trace.final_text:
                trace.error = (
                    stderr.decode("utf-8", errors="replace").strip()[:500] or "claude failed"
                )
            return trace


def parse_stream_json(output: str) -> Trace:
    """Build a :class:`Trace` from Claude Code's ``--output-format stream-json`` lines."""
    trace = Trace()
    pending: dict[str, ToolCall] = {}
    for line in output.splitlines():
        try:
            event: dict[str, Any] = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            trace.model = event.get("model")
            for server in event.get("mcp_servers") or []:
                if server.get("name") == "habitica" and server.get("status") != "connected":
                    trace.error = f"habitica MCP server status: {server.get('status')}"
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") != "tool_use":
                    continue
                tool = strip_server_prefix(str(block.get("name", "")))
                if tool is None:
                    trace.other_tool_calls += 1
                    continue
                call = ToolCall(name=tool, arguments=dict(block.get("input") or {}))
                pending[str(block.get("id"))] = call
                trace.tool_calls.append(call)
        elif kind == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                call = pending.get(str(block.get("tool_use_id")))
                if call is not None:
                    call.is_error = bool(block.get("is_error"))
                    call.result_chars = len(json.dumps(block.get("content"), ensure_ascii=False))
        elif kind == "result":
            trace.final_text = str(event.get("result") or "")
            usage = event.get("usage") or {}
            trace.input_tokens = sum(
                int(usage.get(key) or 0)
                for key in (
                    "input_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                )
            )
            trace.output_tokens = int(usage.get("output_tokens") or 0)
            cost = event.get("total_cost_usd")
            trace.cost_usd = float(cost) if isinstance(cost, int | float) else None
            trace.turns = event.get("num_turns")
            if event.get("is_error") and trace.error is None:
                trace.error = str(event.get("subtype") or "error")
    return trace
