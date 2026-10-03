"""Drive a local Ollama model: a minimal MCP client loop, like an agent app would run."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiohttp
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from mcp_types import TextContent

from .base import ServerSpec, ToolCall, Trace

DEFAULT_HOST = "http://127.0.0.1:11434"
# Tool definitions and instructions take ~4k tokens; Ollama's default context would truncate them.
DEFAULT_NUM_CTX = 16_384
BASE_SYSTEM_PROMPT = (
    "You are an assistant with access to the user's Habitica account through tools. Use the "
    "tools to answer and act; do not invent tasks or results. Answer in the user's language."
)


class OllamaDriver:
    name = "ollama"

    def __init__(
        self,
        model: str,
        *,
        host: str = DEFAULT_HOST,
        max_turns: int = 12,
        timeout_seconds: float = 300.0,
        num_ctx: int = DEFAULT_NUM_CTX,
        server_log: Path | None = None,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.max_turns = max_turns
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.num_ctx = num_ctx
        self.server_log = server_log

    async def run(self, prompt: str, *, server: ServerSpec, system_context: str) -> Trace:
        trace = Trace(model=self.model, input_tokens=0, output_tokens=0, turns=0)
        started = time.perf_counter()
        transport = StdioTransport(
            server.command,
            server.args,
            env=server.env,
            cwd=server.cwd,
            keep_alive=False,
            log_file=self.server_log,
        )
        try:
            async with (
                Client(transport) as mcp,
                aiohttp.ClientSession(timeout=self.timeout) as http,
            ):
                tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or "",
                            "parameters": tool.input_schema,
                        },
                    }
                    for tool in await mcp.list_tools()
                ]
                system = "\n\n".join(
                    part for part in (BASE_SYSTEM_PROMPT, mcp.instructions, system_context) if part
                )
                messages: list[dict[str, Any]] = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ]
                for _ in range(self.max_turns):
                    reply = await self._chat(http, messages, tools)
                    trace.turns = (trace.turns or 0) + 1
                    trace.input_tokens = (trace.input_tokens or 0) + int(
                        reply.get("prompt_eval_count") or 0
                    )
                    trace.output_tokens = (trace.output_tokens or 0) + int(
                        reply.get("eval_count") or 0
                    )
                    message = reply.get("message") or {}
                    messages.append(message)
                    calls = message.get("tool_calls") or []
                    if not calls:
                        trace.final_text = str(message.get("content") or "")
                        break
                    for call in calls:
                        messages.append(await self._execute(mcp, call, trace))
                else:
                    trace.error = "max_turns"
        except (aiohttp.ClientError, TimeoutError, RuntimeError) as exc:
            trace.error = f"{type(exc).__name__}: {exc}"[:500]
        trace.duration_s = time.perf_counter() - started
        return trace

    async def _chat(
        self,
        http: aiohttp.ClientSession,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
        }
        async with http.post(f"{self.host}/api/chat", json=payload) as response:
            if response.status != 200:
                raise RuntimeError(f"Ollama answered {response.status}: {await response.text()}")
            return await response.json()

    async def _execute(
        self, mcp: Client[Any], call: dict[str, Any], trace: Trace
    ) -> dict[str, Any]:
        function = call.get("function") or {}
        name = str(function.get("name", ""))
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {}
        result = await mcp.call_tool(name, arguments, raise_on_error=False)
        text = "\n".join(block.text for block in result.content if isinstance(block, TextContent))
        trace.tool_calls.append(
            ToolCall(
                name=name,
                arguments=dict(arguments),
                is_error=result.is_error,
                result_chars=len(text),
            )
        )
        return {"role": "tool", "tool_name": name, "content": text}
