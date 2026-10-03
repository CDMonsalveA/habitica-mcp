from __future__ import annotations

import pytest

from habitica_mcp.cache import TTLCache
from habitica_mcp.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_limiter_allows_bursts_up_to_the_limit_then_waits_for_the_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(3, 60, clock=clock, sleep=clock.sleep)

    for _ in range(3):
        assert await limiter.acquire() == 0
    assert limiter.time_until_available() == pytest.approx(60)

    waited = await limiter.acquire()

    assert waited == pytest.approx(60)
    assert clock.sleeps == [pytest.approx(60)]


async def test_limiter_frees_slots_as_old_calls_leave_the_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2, 60, clock=clock, sleep=clock.sleep)
    await limiter.acquire()
    clock.now += 30
    await limiter.acquire()
    clock.now += 31  # the first call is now older than the window

    assert await limiter.acquire() == 0


async def test_block_for_pauses_even_with_free_slots() -> None:
    clock = FakeClock()
    limiter = RateLimiter(30, 60, clock=clock, sleep=clock.sleep)
    limiter.block_for(5)

    assert limiter.time_until_available() == pytest.approx(5)
    assert await limiter.acquire() == pytest.approx(5)


def test_limiter_rejects_nonsense_limits() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RateLimiter(0)


def test_cache_expires_entries_and_invalidates_by_prefix() -> None:
    clock = FakeClock()
    cache = TTLCache(10, clock=clock)
    cache.set("tasks:active", [1])
    cache.set("prefs", (300, 0), ttl=100)
    cache.set("disabled", "x", ttl=0)

    assert cache.get("tasks:active") == [1]
    assert cache.get("disabled") is None

    cache.invalidate("tasks:")
    assert cache.get("tasks:active") is None
    assert cache.get("prefs") == (300, 0)

    clock.now += 101
    assert cache.get("prefs") is None

    cache.set("a", 1)
    cache.invalidate()
    assert cache.get("a") is None
