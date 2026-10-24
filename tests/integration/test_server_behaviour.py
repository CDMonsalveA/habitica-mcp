"""Failure handling, rate limiting, metrics, modes and the CLI."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from habitica_mcp.cli import check_connection, main
from habitica_mcp.config import Settings
from habitica_mcp.gateway import HabiticaGateway
from habitica_mcp.server import INSTRUCTIONS, create_server
from tests.fake_habitica import FakeHabitica
from tests.helpers import call, call_error

READ_TOOLS = [
    "habitica_get_overview",
    "habitica_list_tasks",
    "habitica_get_task",
    "habitica_list_tags",
]


async def test_short_retry_after_is_retried_once(client: Client[Any], fake: FakeHabitica) -> None:
    fake.fail_next(429, error="TooManyRequests", message="Too many", headers={"Retry-After": "0"})

    result = await call(client, "habitica_list_tags")

    assert result["status"] == "done"
    assert [r.path for r in fake.api_requests()].count("/api/v3/user") == 2


async def test_long_retry_after_fails_fast_with_guidance(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.fail_next(429, error="TooManyRequests", message="Too many", headers={"Retry-After": "30"})

    message = await call_error(client, "habitica_list_tags")

    assert "rate limit" in message
    assert "30 seconds" in message
    assert len(fake.api_requests()) == 1


async def test_exhausted_server_budget_pauses_further_requests(
    make_settings: Callable[..., Settings], fake: FakeHabitica
) -> None:
    fake.rate_limit = 2
    fake.add_task(type="todo", text="Leer")
    async with Client(create_server(make_settings(cache_ttl_seconds=0))) as client:
        await call(client, "habitica_list_tasks")  # 2 requests: tasks + user
        message = await call_error(client, "habitica_list_tasks")

    assert "rate limit" in message
    assert len(fake.api_requests()) <= 3


async def test_bad_credentials_are_explained(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(api_token="00000000-0000-4000-8000-0000000000bb")
    async with Client(create_server(settings)) as client:
        message = await call_error(client, "habitica_get_overview")

    assert message.startswith("Habitica rejected the credentials: There is no account")


async def test_missing_credentials_are_explained_without_breaking_tool_listing() -> None:
    async with Client(create_server(Settings())) as client:
        tools = await client.list_tools()
        message = await call_error(client, "habitica_list_tasks")

    assert len(tools) == 10
    assert "HABITICA_USER_ID and HABITICA_API_TOKEN" in message


async def test_unreachable_habitica(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(base_url="http://127.0.0.1:9", request_timeout_seconds=2)
    async with Client(create_server(settings)) as client:
        message = await call_error(client, "habitica_list_tags")

    assert message.startswith("Could not reach Habitica")


async def test_unexpected_errors_are_masked(
    client: Client[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explode(self: HabiticaGateway) -> None:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(HabiticaGateway, "get_context", explode)

    message = await call_error(client, "habitica_list_tags")

    assert message == "Error calling tool 'habitica_list_tags'"


async def test_server_errors_and_unexpected_payloads(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.fail_next(503, message="Maintenance")
    outage = await call_error(client, "habitica_list_tags")
    assert outage == "Habitica had an internal problem (HTTP 503). Try again later."


async def test_metrics_file_records_counters_without_user_content(
    make_settings: Callable[..., Settings], fake: FakeHabitica, tmp_path: Path
) -> None:
    fake.add_task(type="todo", text="Texto privado")
    metrics = tmp_path / "metrics.jsonl"
    async with Client(create_server(make_settings(metrics_file=metrics))) as client:
        await call(client, "habitica_list_tasks")
        await call(client, "habitica_list_tasks")
        await call_error(client, "habitica_get_task", task="no existe")

    lines = [json.loads(line) for line in metrics.read_text(encoding="utf-8").splitlines()]
    assert [(r["tool"], r["outcome"]) for r in lines] == [
        ("habitica_list_tasks", "ok"),
        ("habitica_list_tasks", "ok"),
        ("habitica_get_task", "tool_error"),
    ]
    assert (lines[0]["habitica_calls"], lines[1]["habitica_calls"]) == (2, 0)
    assert lines[1]["cache_hits"] >= 2
    assert lines[2]["error_type"] == "TaskNotFoundError"
    assert "Texto privado" not in metrics.read_text(encoding="utf-8")


async def test_read_only_mode_only_exposes_read_tools(
    make_settings: Callable[..., Settings],
) -> None:
    async with Client(create_server(make_settings(mode="read-only"))) as client:
        names = [tool.name for tool in await client.list_tools()]
    assert names == READ_TOOLS


async def test_tool_metadata_marks_risk(client: Client[Any]) -> None:
    tools = {tool.name: tool for tool in await client.list_tools()}

    for name in READ_TOOLS:
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is True
    for name in ("habitica_delete_task", "habitica_manage_checklist", "habitica_manage_tags"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.destructive_hint is True
    assert "$ref" not in json.dumps([tool.input_schema for tool in tools.values()])
    assert "confirm=true" in INSTRUCTIONS


async def test_check_connection_reports_the_account(
    make_settings: Callable[..., Settings], capsys: pytest.CaptureFixture[str]
) -> None:
    assert await check_connection(make_settings()) == 0
    assert "as Tester (level 5)" in capsys.readouterr().out

    assert await check_connection(Settings()) == 1
    assert "credentials are not configured" in capsys.readouterr().err


def test_cli_version_and_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as version:
        main(["--version"])
    assert version.value.code == 0
    assert capsys.readouterr().out.startswith("habitica-mcp ")

    monkeypatch.setenv("HABITICA_MCP_RATE_LIMIT", "100")
    with pytest.raises(SystemExit) as invalid:
        main([])
    assert invalid.value.code == 2
    assert "HABITICA_MCP_RATE_LIMIT" in capsys.readouterr().err


async def test_parallel_tool_calls_share_one_request(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.add_task(type="todo", text="Leer")

    results = await asyncio.gather(
        call(client, "habitica_list_tasks"),
        call(client, "habitica_list_tasks", status="pending"),
        call(client, "habitica_list_tags"),
    )

    assert all(result for result in results)
    paths = [r.path for r in fake.api_requests()]
    assert paths.count("/api/v3/tasks/user") == 1
    assert paths.count("/api/v3/user") == 1


def test_plain_http_to_a_remote_host_is_flagged() -> None:
    def insecure(url: str) -> bool:
        return Settings.model_validate({"base_url": url}).sends_credentials_in_clear

    assert insecure("http://habitica.example.org") is True
    assert insecure("https://habitica.com") is False
    assert insecure("http://127.0.0.1:3000") is False
    assert insecure("http://habitica.localhost") is False
