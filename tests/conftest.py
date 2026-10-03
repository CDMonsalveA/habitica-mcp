from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from aiohttp.test_utils import TestServer
from fastmcp import Client

from habitica_mcp.config import Settings
from habitica_mcp.server import create_server
from tests.fake_habitica import FakeHabitica
from tests.helpers import FIXED_NOW, UTC_MINUS_5


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's real HABITICA_* variables out of the tests."""
    for key in list(os.environ):
        if key.upper().startswith("HABITICA_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def fake() -> FakeHabitica:
    fake = FakeHabitica(now=lambda: FIXED_NOW)
    fake.user["preferences"]["timezoneOffset"] = UTC_MINUS_5
    return fake


@pytest.fixture
async def fake_url(fake: FakeHabitica) -> AsyncIterator[str]:
    server = TestServer(fake.app())
    await server.start_server()
    try:
        yield str(server.make_url("")).rstrip("/")
    finally:
        await server.close()


@pytest.fixture
def make_settings(fake: FakeHabitica, fake_url: str) -> Callable[..., Settings]:
    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "user_id": fake.user_id,
            "api_token": fake.api_token,
            "base_url": fake_url,
            "now": FIXED_NOW,
            "rate_limit_per_minute": 30,
            "max_rate_limit_wait_seconds": 2.0,
        }
        values.update(overrides)
        return Settings(**values)

    return factory


@pytest.fixture
async def client(make_settings: Callable[..., Settings]) -> AsyncIterator[Client[Any]]:
    async with Client(create_server(make_settings())) as mcp_client:
        yield mcp_client
