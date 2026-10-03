"""Per-tool-call metrics.

The middleware opens a :class:`ToolCallStats` for every ``tools/call``; the gateway
records Habitica requests, cache hits and rate-limit waits into it through a context
variable. When the call ends one JSON line is logged (and optionally appended to a
file) with no user content: tool name, outcome, duration and counters only.
"""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, override

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp_types import CallToolRequestParams

from .errors import HabiticaMCPError

logger = logging.getLogger("habitica_mcp.metrics")


@dataclass(slots=True)
class ToolCallStats:
    habitica_calls: int = 0
    cache_hits: int = 0
    rate_limit_wait_seconds: float = 0.0
    rate_limited_responses: int = 0


_current_stats: ContextVar[ToolCallStats | None] = ContextVar("habitica_mcp_stats", default=None)


def record_api_call() -> None:
    if (stats := _current_stats.get()) is not None:
        stats.habitica_calls += 1


def record_cache_hit() -> None:
    if (stats := _current_stats.get()) is not None:
        stats.cache_hits += 1


def record_rate_limit_wait(seconds: float) -> None:
    if seconds > 0 and (stats := _current_stats.get()) is not None:
        stats.rate_limit_wait_seconds += seconds


def record_rate_limited_response() -> None:
    if (stats := _current_stats.get()) is not None:
        stats.rate_limited_responses += 1


class ToolCallMiddleware(Middleware):
    """Measures every tool call and classifies its outcome."""

    def __init__(self, metrics_file: Path | None = None) -> None:
        self._metrics_file = metrics_file

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        stats = ToolCallStats()
        token = _current_stats.set(stats)
        started = time.perf_counter()
        outcome, error_type = "ok", None
        try:
            return await call_next(context)
        except ToolError as exc:
            # FastMCP wraps unexpected exceptions in a masked ToolError; ours pass through.
            cause = exc.__cause__
            if isinstance(exc, HabiticaMCPError):
                outcome, error_type = "tool_error", type(exc).__name__
            elif cause is not None:
                outcome, error_type = "error", type(cause).__name__
            else:
                outcome, error_type = "tool_error", type(exc).__name__
            raise
        except Exception as exc:
            outcome, error_type = "error", type(exc).__name__
            raise
        finally:
            _current_stats.reset(token)
            self._emit(
                {
                    "event": "tool_call",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "tool": context.message.name,
                    "outcome": outcome,
                    "error_type": error_type,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    **asdict(stats),
                }
            )

    def _emit(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        logger.info(line)
        if self._metrics_file is not None:
            try:
                with self._metrics_file.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                logger.warning("Could not write metrics to %s", self._metrics_file)
