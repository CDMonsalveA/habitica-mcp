"""Client-side rate limiting for Habitica's API.

Habitica allows 30 requests per minute per user and can block a whole ``x-client``
value that misbehaves, which would break the server for every user. The limiter keeps
a sliding window below that limit and also obeys the server's own signals
(``X-RateLimit-Remaining`` / ``Retry-After``), since other apps of the same user share
the budget.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

from .const import RATE_LIMIT_PERIOD_SECONDS

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class RateLimiter:
    """Sliding-window limiter shared by every request of one server process."""

    def __init__(
        self,
        max_calls: int,
        period: float = RATE_LIMIT_PERIOD_SECONDS,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be at least 1")
        self._max_calls = max_calls
        self._period = period
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._blocked_until = 0.0
        self._lock = asyncio.Lock()

    def _prune(self, now: float) -> None:
        while self._calls and now - self._calls[0] >= self._period:
            self._calls.popleft()

    def time_until_available(self) -> float:
        """Seconds until a request could start, without reserving a slot."""
        now = self._clock()
        self._prune(now)
        wait = max(self._blocked_until - now, 0.0)
        if len(self._calls) >= self._max_calls:
            wait = max(wait, self._calls[0] + self._period - now)
        return wait

    async def acquire(self) -> float:
        """Reserve a request slot, sleeping if needed. Returns the seconds waited."""
        waited = 0.0
        async with self._lock:
            while (wait := self.time_until_available()) > 0:
                await self._sleep(wait)
                waited += wait
            self._calls.append(self._clock())
        return waited

    def block_for(self, seconds: float) -> None:
        """Pause all requests, e.g. after Habitica answered 429 with ``Retry-After``."""
        if seconds > 0:
            self._blocked_until = max(self._blocked_until, self._clock() + seconds)
