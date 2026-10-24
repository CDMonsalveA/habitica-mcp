"""HTTP access to Habitica's v3 API.

One method maps to one API call (plus caching). Every request goes through the shared
rate limiter, sends the ``x-client`` header Habitica requires, and turns failures into
:mod:`habitica_mcp.errors` exceptions with model-readable messages. Response bodies are
parsed with habiticalib's typed models.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import aiohttp
from habiticalib import Direction, TagsUser, TaskData, TaskFilter, TaskType, UserData
from multidict import CIMultiDictProxy

from . import __version__
from .cache import TTLCache
from .const import APP_NAME, PREFERENCES_TTL_SECONDS, RATE_LIMIT_PERIOD_SECONDS, REPOSITORY_URL
from .dates import UserClock
from .errors import (
    HabiticaAPIError,
    HabiticaConnectionError,
    RateLimitedError,
    UnexpectedResponseError,
)
from .metrics import (
    record_api_call,
    record_cache_hit,
    record_rate_limit_wait,
    record_rate_limited_response,
)
from .ratelimit import RateLimiter

logger = logging.getLogger(__name__)

USER_AGENT = f"{APP_NAME}/{__version__} (+{REPOSITORY_URL})"
CLOCK_FIELDS = ("preferences.timezoneOffset", "preferences.dayStart")
# Habitica only computes ``needsCron`` for the whole user document; these let us derive it.
CRON_FIELDS = ("lastCron", "preferences.timezoneOffsetAtLastCron")
# What each kind of write can change. Narrow invalidation matters: Habitica allows only 30
# requests per minute, and agents often chain several writes.
_TASK_WRITE = ("tasks:", "task:")  # create/update/checklist: the returned task refreshes the list
_SCORE_WRITE = ("tasks:", "task:", "user:")  # scoring also changes stats, streaks, completion
_TAG_WRITE = ("tags", "tasks:", "task:", "user:")  # deleting a tag removes it from tasks
_ACTIVE_TASKS = "tasks:active"


class HabiticaGateway:
    """Rate-limited, cached client for the parts of Habitica's API this server uses."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        base_url: str,
        user_id: str,
        api_token: str,
        x_client: str,
        limiter: RateLimiter,
        cache: TTLCache,
        now: Callable[[], datetime],
        timeout_seconds: float = 20.0,
        max_wait_seconds: float = 10.0,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "x-api-user": user_id,
            "x-api-key": api_token,
            "x-client": x_client,
            "User-Agent": USER_AGENT,
        }
        self._limiter = limiter
        self._cache = cache
        self._now = now
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_wait = max_wait_seconds
        self._inflight: dict[str, asyncio.Future[Any]] = {}

    # ------------------------------------------------------------------ reads

    async def get_user(self, fields: Sequence[str]) -> UserData:
        """Fetch only the listed user fields (``userFields`` projection)."""
        wanted = sorted(set(fields))

        async def load() -> UserData:
            params = {"userFields": ",".join(wanted)}
            user = _parse(
                UserData,
                await self._request("GET", "/api/v3/user", params=params),
                "the user profile",
            )
            if all(field in wanted for field in CLOCK_FIELDS):
                self._cache.set("prefs", _clock_prefs(user), ttl=PREFERENCES_TTL_SECONDS)
            if "tags" in wanted:
                self._cache.set("tags", list(user.tags))
            return user

        return await self._cached("user:" + ",".join(wanted), load)

    async def get_full_user(self) -> UserData:
        """The whole user document (large). Only it carries ``needsCron`` and computed stats."""

        async def load() -> UserData:
            return _parse(UserData, await self._request("GET", "/api/v3/user"), "the user profile")

        return await self._cached("user:*", load)

    async def get_context(self) -> tuple[UserClock, list[TagsUser]]:
        """The user's clock and tags, fetched together in at most one call."""
        prefs: tuple[int, int] | None = self._cache.get("prefs")
        tags: list[TagsUser] | None = self._cache.get("tags")
        if prefs is None or tags is None:
            user = await self.get_user(("tags", *CLOCK_FIELDS))
            prefs, tags = _clock_prefs(user), list(user.tags)
        else:
            record_cache_hit()
        return self.clock_from_prefs(prefs), tags

    def clock_from_prefs(self, prefs: tuple[int, int]) -> UserClock:
        offset, day_start = prefs
        return UserClock(
            now_utc=self._now(), timezone_offset_minutes=offset, day_start_hour=day_start
        )

    def clock_for(self, user: UserData) -> UserClock:
        return self.clock_from_prefs(_clock_prefs(user))

    async def get_tasks(self, task_filter: TaskFilter | None = None) -> list[TaskData]:
        """Active tasks (or one type / the 30 latest completed to-dos), without history."""
        key = f"tasks:{task_filter.value if task_filter else 'active'}"
        params = {"history": "false"}
        if task_filter is not None:
            params["type"] = task_filter.value

        async def load() -> list[TaskData]:
            data = await self._request("GET", "/api/v3/tasks/user", params=params)
            if not isinstance(data, list):
                raise UnexpectedResponseError("the task list")
            return [_parse(TaskData, item, "a task") for item in data]  # pyright: ignore[reportUnknownVariableType]

        return await self._cached(key, load)

    async def get_task(self, task_id: str) -> TaskData:
        async def load() -> TaskData:
            data = await self._request("GET", f"/api/v3/tasks/{_segment(task_id)}")
            return _parse(TaskData, data, "the task")

        return await self._cached(f"task:{task_id}", load)

    async def get_tags(self) -> list[TagsUser]:
        async def load() -> list[TagsUser]:
            data = await self._request("GET", "/api/v3/tags")
            if not isinstance(data, list):
                raise UnexpectedResponseError("the tag list")
            return [_parse(TagsUser, item, "a tag") for item in data]  # pyright: ignore[reportUnknownVariableType]

        return await self._cached("tags", load)

    # ----------------------------------------------------------------- writes

    async def create_task(self, body: Mapping[str, Any]) -> TaskData:
        return await self._write_task("POST", "/api/v3/tasks/user", body)

    async def update_task(self, task_id: str, body: Mapping[str, Any]) -> TaskData:
        return await self._write_task("PUT", f"/api/v3/tasks/{_segment(task_id)}", body)

    async def delete_task(self, task_id: str) -> None:
        snapshot: list[TaskData] | None = self._cache.get(_ACTIVE_TASKS)
        await self._write("DELETE", f"/api/v3/tasks/{_segment(task_id)}", invalidate=_TASK_WRITE)
        if snapshot is not None:
            self._cache.set(_ACTIVE_TASKS, [t for t in snapshot if str(t.id) != task_id])

    async def score_task(self, task_id: str, direction: Direction) -> dict[str, Any]:
        """Score a task. Returns Habitica's raw stats payload (``delta``, ``_tmp``...)."""
        path = f"/api/v3/tasks/{_segment(task_id)}/score/{direction.value}"
        data = await self._write("POST", path, invalidate=_SCORE_WRITE)
        if not isinstance(data, dict):
            raise UnexpectedResponseError("the score result")
        return data  # pyright: ignore[reportUnknownVariableType]

    async def add_checklist_item(self, task_id: str, text: str) -> TaskData:
        path = f"/api/v3/tasks/{_segment(task_id)}/checklist"
        return await self._write_task("POST", path, {"text": text})

    async def update_checklist_item(self, task_id: str, item_id: str, text: str) -> TaskData:
        path = f"/api/v3/tasks/{_segment(task_id)}/checklist/{_segment(item_id)}"
        return await self._write_task("PUT", path, {"text": text})

    async def score_checklist_item(self, task_id: str, item_id: str) -> TaskData:
        path = f"/api/v3/tasks/{_segment(task_id)}/checklist/{_segment(item_id)}/score"
        return await self._write_task("POST", path)

    async def delete_checklist_item(self, task_id: str, item_id: str) -> TaskData:
        path = f"/api/v3/tasks/{_segment(task_id)}/checklist/{_segment(item_id)}"
        return await self._write_task("DELETE", path)

    async def create_tag(self, name: str) -> TagsUser:
        data = await self._write("POST", "/api/v3/tags", {"name": name}, invalidate=_TAG_WRITE)
        return _parse(TagsUser, data, "the tag")

    async def update_tag(self, tag_id: str, name: str) -> TagsUser:
        path = f"/api/v3/tags/{_segment(tag_id)}"
        data = await self._write("PUT", path, {"name": name}, invalidate=_TAG_WRITE)
        return _parse(TagsUser, data, "the tag")

    async def delete_tag(self, tag_id: str) -> None:
        await self._write("DELETE", f"/api/v3/tags/{_segment(tag_id)}", invalidate=_TAG_WRITE)

    # --------------------------------------------------------------- plumbing

    async def _cached[T](self, key: str, load: Callable[[], Awaitable[T]]) -> T:
        """Serve from the cache, or share one in-flight request between concurrent calls.

        Clients often run several tools in parallel; without this each would spend a request
        of Habitica's per-minute budget on the same data.
        """
        if (cached := self._cache.get(key)) is not None:
            record_cache_hit()
            return cached  # type: ignore[no-any-return]
        if (pending := self._inflight.get(key)) is not None:
            record_cache_hit()
            return await asyncio.shield(pending)  # type: ignore[no-any-return]
        task: asyncio.Future[T] = asyncio.ensure_future(load())
        self._inflight[key] = task
        try:
            value = await asyncio.shield(task)
        finally:
            self._inflight.pop(key, None)
        self._cache.set(key, value)
        return value

    async def _write(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        invalidate: Sequence[str],
    ) -> Any:
        try:
            return await self._request(method, path, json_body=body)
        finally:
            # Even a failed write may have changed something server-side.
            self._cache.invalidate(*invalidate)

    async def _write_task(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> TaskData:
        """A write that returns the task: patch it into the cached task list, don't refetch."""
        snapshot: list[TaskData] | None = self._cache.get(_ACTIVE_TASKS)
        data = await self._write(method, path, body, invalidate=_TASK_WRITE)
        task = _parse(TaskData, data, "the task")
        if snapshot is not None:
            self._cache.set(_ACTIVE_TASKS, _upsert(snapshot, task))
        return task

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> Any:
        """Send one request and return the ``data`` member of Habitica's envelope."""
        retried = False
        while True:
            wait = self._limiter.time_until_available()
            if wait > self._max_wait:
                raise RateLimitedError(wait)
            record_rate_limit_wait(await self._limiter.acquire())
            record_api_call()
            logger.debug("%s %s", method, path)
            try:
                async with self._session.request(
                    method,
                    self._base_url + path,
                    params=params,
                    json=json_body,
                    headers=self._headers,
                    timeout=self._timeout,
                ) as response:
                    status = response.status
                    headers = response.headers
                    text = await response.text()
            except TimeoutError as exc:
                raise HabiticaConnectionError("the request timed out") from exc
            except aiohttp.ClientError as exc:
                raise HabiticaConnectionError(type(exc).__name__) from exc

            self._observe_rate_limit(headers)
            if status == 429:
                record_rate_limited_response()
                retry_after = _retry_after_seconds(headers)
                self._limiter.block_for(retry_after)
                if not retried and retry_after <= self._max_wait:
                    retried = True
                    continue
                raise RateLimitedError(retry_after, by_habitica=True)

            payload = _json_or_none(text)
            if status >= 400:
                raise HabiticaAPIError.from_payload(status, payload)
            if not isinstance(payload, dict) or "data" not in payload:
                raise UnexpectedResponseError(f"a response to {method} {path}")
            return payload["data"]  # pyright: ignore[reportUnknownVariableType]

    def _observe_rate_limit(self, headers: CIMultiDictProxy[str]) -> None:
        """Pause early when Habitica says the per-minute budget is exhausted."""
        remaining = headers.get("X-RateLimit-Remaining")
        if remaining is None:
            return
        try:
            if int(float(remaining)) > 0:
                return
        except ValueError:
            return
        reset = _parse_reset(headers.get("X-RateLimit-Reset"))
        seconds = (
            (reset - datetime.now(UTC)).total_seconds() if reset else RATE_LIMIT_PERIOD_SECONDS
        )
        self._limiter.block_for(min(max(seconds, 0.0), RATE_LIMIT_PERIOD_SECONDS))


def _segment(value: str) -> str:
    return quote(value, safe="")


def _upsert(tasks: Sequence[TaskData], task: TaskData) -> list[TaskData]:
    """Replace (or append) ``task`` in an active-task list; completed to-dos are not active."""
    active = not (task.Type is TaskType.TODO and task.completed)
    result: list[TaskData] = []
    replaced = False
    for existing in tasks:
        if existing.id == task.id:
            replaced = True
            if active:
                result.append(task)
        else:
            result.append(existing)
    if not replaced and active:
        result.append(task)
    return result


def _json_or_none(text: str) -> Any:
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def _parse[T](model: type[T], data: Any, what: str) -> T:
    if not isinstance(data, dict):
        raise UnexpectedResponseError(what)
    try:
        return model.from_dict(data)  # type: ignore[attr-defined,no-any-return]
    except Exception as exc:  # mashumaro raises several unrelated types
        # The exception text can quote the offending value (task text...): log only metadata.
        field = getattr(exc, "field_name", None)
        logger.warning("Could not parse %s (%s, field %s)", what, type(exc).__name__, field)
        raise UnexpectedResponseError(what) from exc


def _clock_prefs(user: UserData) -> tuple[int, int]:
    prefs = user.preferences
    return int(prefs.timezoneOffset or 0), int(prefs.dayStart or 0)


def _retry_after_seconds(headers: CIMultiDictProxy[str]) -> float:
    try:
        return max(float(headers.get("Retry-After", "")), 0.0)
    except ValueError:
        return RATE_LIMIT_PERIOD_SECONDS


def _parse_reset(value: str | None) -> datetime | None:
    """Habitica sends a JavaScript date string, e.g. 'Mon Sep 14 2026 17:31:00 GMT+0000 (...)'."""
    if not value:
        return None
    try:
        return datetime.strptime(value[:33], "%a %b %d %Y %H:%M:%S %Z%z")
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
