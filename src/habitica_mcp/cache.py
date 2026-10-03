"""A tiny TTL cache so agent loops don't re-download the same data."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

Clock = Callable[[], float]


class TTLCache:
    """In-memory key/value cache with per-entry expiry."""

    def __init__(self, default_ttl: float, *, clock: Clock = time.monotonic) -> None:
        self._default_ttl = default_ttl
        self._clock = clock
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        """Return the cached value, or ``None`` when missing or expired."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        ttl = self._default_ttl if ttl is None else ttl
        if ttl <= 0:
            return
        self._entries[key] = (self._clock() + ttl, value)

    def invalidate(self, *prefixes: str) -> None:
        """Drop entries whose key starts with any prefix (all entries if none given)."""
        if not prefixes:
            self._entries.clear()
            return
        for key in [k for k in self._entries if k.startswith(prefixes)]:
            del self._entries[key]
