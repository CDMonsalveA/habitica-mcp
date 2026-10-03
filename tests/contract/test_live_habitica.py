"""Contract tests against the real Habitica API.

They detect Habitica changes that the in-memory test double would hide. They need a
dedicated test account (never a personal one) and run weekly in CI or on demand:

    HABITICA_TEST_USER_ID=... HABITICA_TEST_API_TOKEN=... uv run pytest -m contract

The whole module makes about 25 requests, under Habitica's 30 per minute.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import pytest
from fastmcp import Client

from habitica_mcp.config import Settings
from habitica_mcp.const import DEFAULT_BASE_URL
from habitica_mcp.server import create_server
from tests.helpers import call

# Read at import time: the autouse fixture in conftest removes HABITICA_* variables.
USER_ID = os.environ.get("HABITICA_TEST_USER_ID")
API_TOKEN = os.environ.get("HABITICA_TEST_API_TOKEN")
BASE_URL = os.environ.get("HABITICA_TEST_BASE_URL", DEFAULT_BASE_URL)

pytestmark = [
    pytest.mark.contract,
    pytest.mark.skipif(not (USER_ID and API_TOKEN), reason="HABITICA_TEST_* variables not set"),
]

# Fields the server reads; if Habitica drops or renames one of them, these tests fail.
TASK_KEYS = {
    "id",
    "type",
    "text",
    "notes",
    "tags",
    "priority",
    "completed",
    "checklist",
    "challenge",
}
# Computed stats (toNextLevel, maxHealth, maxMP) only come with the whole user document.
USER_STATS_KEYS = {"hp", "mp", "exp", "gp", "lvl", "class"}


@pytest.fixture
async def live_client() -> AsyncIterator[Client[Any]]:
    settings = Settings.model_validate(
        {"user_id": USER_ID, "api_token": API_TOKEN, "base_url": BASE_URL, "cache_ttl_seconds": 0}
    )
    async with Client(create_server(settings)) as client:
        yield client


async def raw_get(path: str, params: dict[str, str]) -> Any:
    headers = {
        "x-api-user": USER_ID or "",
        "x-api-key": API_TOKEN or "",
        "x-client": f"{USER_ID}-habitica-mcp-contract-tests",
    }
    async with (
        aiohttp.ClientSession() as session,
        session.get(BASE_URL + path, params=params, headers=headers) as response,
    ):
        assert response.status == 200, await response.text()
        body = await response.json()
    assert body["success"] is True
    return body["data"]


async def test_raw_shapes_still_contain_the_fields_we_read() -> None:
    fields = (
        "profile.name,stats,tags,lastCron,preferences.timezoneOffset,preferences.dayStart,"
        "preferences.timezoneOffsetAtLastCron"
    )
    user = await raw_get("/api/v3/user", {"userFields": fields})
    # Report only key names on failure: the document holds personal data.
    stats_keys, pref_keys = sorted(user.get("stats", {})), sorted(user.get("preferences", {}))
    assert USER_STATS_KEYS.issubset(stats_keys), stats_keys
    assert {"timezoneOffset", "dayStart"}.issubset(pref_keys), pref_keys
    assert "lastCron" in user, sorted(user)

    full = await raw_get("/api/v3/user", {})
    assert isinstance(full.get("needsCron"), bool), "needsCron missing from the full document"

    todos = await raw_get("/api/v3/tasks/user", {"type": "todos", "history": "false"})
    assert isinstance(todos, list)
    for todo in todos[:5]:
        assert TASK_KEYS.issubset(todo), sorted(TASK_KEYS - set(todo))  # key names only


async def test_task_lifecycle_through_the_mcp_tools(live_client: Client[Any]) -> None:
    marker = uuid.uuid4().hex[:8]
    text = f"habitica-mcp contract test {marker}"
    tag = f"habitica-mcp-contract-{marker}"
    task_id: str | None = None
    try:
        overview = await call(live_client, "habitica_get_overview")
        assert overview["player"]["level"] >= 1

        created = await call(
            live_client,
            "habitica_create_task",
            task_type="todo",
            text=text,
            tags=[tag],
            checklist=["first item"],
            due_date=overview["today"],
        )
        task_id = created["task"]["id"]
        assert created["task"]["due_date"] == overview["today"], (
            "due dates must stay on the same local day"
        )
        assert created["created_tags"] == [tag]

        toggled = await call(
            live_client,
            "habitica_manage_checklist",
            task=task_id,
            action="toggle",
            item="first item",
        )
        assert toggled["task"]["checklist_progress"] == "1/1"

        scored = await call(live_client, "habitica_score_task", task=task_id)
        assert scored["status"] == "done"
        assert scored["stats"]["level"] >= 1

        undone = await call(live_client, "habitica_score_task", task=task_id, direction="down")
        assert undone["status"] == "done"

        deleted = await call(live_client, "habitica_delete_task", task=task_id, confirm=True)
        assert deleted["status"] == "done"
        task_id = None
    finally:
        if task_id is not None:
            await live_client.call_tool(
                "habitica_delete_task", {"task": task_id, "confirm": True}, raise_on_error=False
            )
        await live_client.call_tool(
            "habitica_manage_tags",
            {"action": "delete", "tag": tag, "confirm": True},
            raise_on_error=False,
        )
