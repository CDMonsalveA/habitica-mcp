"""Per-process state shared by tool calls: settings, HTTP session, limiter and cache."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import aiohttp

from .cache import TTLCache
from .config import Settings
from .gateway import HabiticaGateway
from .ratelimit import RateLimiter


class AppState:
    """Creates the Habitica gateway lazily, so the server starts without credentials."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.limiter = RateLimiter(settings.rate_limit_per_minute)
        self.cache = TTLCache(settings.cache_ttl_seconds)
        self._session: aiohttp.ClientSession | None = None
        self._gateway: HabiticaGateway | None = None
        self._lock = asyncio.Lock()

    def now(self) -> datetime:
        return self.settings.now or datetime.now(UTC)

    async def gateway(self) -> HabiticaGateway:
        if self._gateway is not None:
            return self._gateway
        async with self._lock:
            if self._gateway is None:
                user_id, api_token = self.settings.credentials()
                self._session = aiohttp.ClientSession()
                self._gateway = HabiticaGateway(
                    self._session,
                    base_url=self.settings.base_url,
                    user_id=user_id,
                    api_token=api_token,
                    x_client=self.settings.resolved_x_client(),
                    limiter=self.limiter,
                    cache=self.cache,
                    now=self.now,
                    timeout_seconds=self.settings.request_timeout_seconds,
                    max_wait_seconds=self.settings.max_rate_limit_wait_seconds,
                )
        return self._gateway

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
            self._gateway = None
