"""Run the real ``python -m habitica_mcp`` process over stdio, as MCP clients do."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from tests.fake_habitica import FakeHabitica
from tests.helpers import FIXED_NOW, call

PROJECT_ROOT = Path(__file__).resolve().parents[2]


async def test_stdio_server_lists_tools_and_completes_a_todo(
    fake: FakeHabitica, fake_url: str, tmp_path: Path
) -> None:
    task = fake.add_task(type="todo", text="Probar por stdio")
    env = {
        **{k: v for k, v in os.environ.items() if not k.upper().startswith("HABITICA_")},
        "HABITICA_USER_ID": fake.user_id,
        "HABITICA_API_TOKEN": fake.api_token,
        "HABITICA_BASE_URL": fake_url,
        "HABITICA_MCP_NOW": FIXED_NOW.isoformat(),
    }
    # The server logs to stderr; send it to a real file (pytest's captured stderr can block it).
    server_log = tmp_path / "server.log"
    transport = StdioTransport(
        sys.executable,
        ["-m", "habitica_mcp"],
        env=env,
        cwd=str(PROJECT_ROOT),
        log_file=server_log,
        keep_alive=False,
    )

    async with Client(transport) as client:
        tools = await client.list_tools()
        result = await call(client, "habitica_score_task", task="stdio")

    assert len(tools) == 10
    assert result["message"] == "Completed the to-do 'Probar por stdio'."
    assert fake.tasks[task["id"]]["completed"] is True
    assert fake.api_token not in server_log.read_text(encoding="utf-8", errors="replace")
