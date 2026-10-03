"""An in-memory stand-in for the parts of Habitica's API that habitica-mcp uses.

It mirrors the behaviors that matter for the MCP server and were checked against
HabitRPG/habitica's source: the response envelope, the ``x-client`` requirement,
``userFields`` projection, ``history=false``, rate-limit headers and 429s, and the
business rules that surface as errors (401 "Not Enough Gold", challenge tasks...).
Game maths (XP, gold, damage) is deliberately simplified.

Besides the API it exposes control endpoints for evals: ``GET /__state``,
``POST /__reset`` (body: a seed) and ``GET /__requests``.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from aiohttp import web

DEFAULT_USER_ID = "00000000-0000-4000-8000-000000000001"
DEFAULT_API_TOKEN = "00000000-0000-4000-8000-0000000000aa"
APP_VERSION = "5.50.3"
WEEKDAY_KEYS = ("m", "t", "w", "th", "f", "s", "su")
TASK_TYPES = ("habit", "daily", "todo", "reward")
UPDATABLE_FIELDS = {
    "text", "notes", "priority", "attribute", "date", "tags", "checklist", "collapseChecklist",
    "up", "down", "value", "frequency", "everyX", "repeat", "daysOfMonth", "weeksOfMonth",
    "startDate", "alias", "reminders", "streak",
}  # fmt: skip
CHALLENGE_UPDATABLE_FIELDS = {"notes", "tags", "checklist", "attribute", "reminders", "alias"}
PRIORITY_REWARD = {0.1: 1, 1: 2, 1.5: 3, 2: 4}
JSON_BODY = web.RequestKey[dict[str, Any]]("json_body", dict)


@dataclass
class RecordedRequest:
    method: str
    path: str
    query: dict[str, str]
    body: Any
    headers: dict[str, str]


@dataclass
class ForcedFailure:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


def _iso(instant: datetime) -> str:
    return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class FakeHabitica:
    """State plus an aiohttp application that serves it."""

    def __init__(
        self,
        *,
        user_id: str = DEFAULT_USER_ID,
        api_token: str = DEFAULT_API_TOKEN,
        now: Callable[[], datetime] | None = None,
        rate_limit: int = 30,
    ) -> None:
        self.user_id = user_id
        self.api_token = api_token
        self.now = now or (lambda: datetime.now(UTC))
        self.rate_limit = rate_limit
        self.requests: list[RecordedRequest] = []
        self._failures: list[ForcedFailure] = []
        self._window_start: datetime | None = None
        self._window_count = 0
        self.next_drop: dict[str, Any] | None = None
        self.user: dict[str, Any] = {}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.reset()

    # ------------------------------------------------------------------ seeding

    def reset(self, seed: Mapping[str, Any] | None = None) -> None:
        """Start over with a default user, optionally applying a seed.

        Seed keys: ``user`` (deep-merged into the user document), ``tags`` (names or
        ``{id, name, challenge}``) and ``tasks`` (dicts accepted by :meth:`add_task`).
        """
        now = self.now()
        self.user = {
            "_id": self.user_id,
            "id": self.user_id,
            "profile": {"name": "Tester"},
            "stats": {
                "hp": 50.0, "mp": 30.0, "exp": 10, "gp": 25.0, "lvl": 5, "class": "warrior",
                "points": 0,
                "buffs": {}, "training": {},
            },
            "preferences": {"timezoneOffset": 0, "dayStart": 0, "language": "en"},
            "lastCron": _iso(now),
            "tags": [],
            "flags": {},
        }  # fmt: skip
        self.tasks = {}
        self.requests.clear()
        self._failures.clear()
        self._window_start = None
        self._window_count = 0
        self.next_drop = None
        if not seed:
            return
        _deep_merge(self.user, seed.get("user", {}))
        for tag in seed.get("tags", []):
            if isinstance(tag, str):
                self.add_tag(tag)
            else:
                self.add_tag(
                    tag["name"], tag_id=tag.get("id"), challenge=tag.get("challenge", False)
                )
        for task in seed.get("tasks", []):
            self.add_task(**task)

    def add_tag(
        self, name: str, *, tag_id: str | None = None, challenge: bool = False
    ) -> dict[str, Any]:
        tag: dict[str, Any] = {"id": tag_id or str(uuid.uuid4()), "name": name}
        if challenge:
            tag["challenge"] = True
        self.user["tags"].append(tag)
        return tag

    def tag_id(self, name: str) -> str:
        return next(t["id"] for t in self.user["tags"] if t["name"] == name)

    def add_task(self, **fields: Any) -> dict[str, Any]:
        """Create a task directly in the state. ``tags`` may contain tag names."""
        fields = dict(fields)
        kind = fields.pop("type")
        text = fields.pop("text")
        tag_refs = fields.pop("tags", [])
        task = self._new_task(kind, text)
        task["tags"] = [self._tag_ref_to_id(ref) for ref in tag_refs]
        if "checklist" in fields:
            fields["checklist"] = [self._checklist_item(item) for item in fields["checklist"]]
        due_in_days = fields.pop("due_in_days", None)
        if due_in_days is not None:
            fields["date"] = _iso(self._local_midnight(self.today() + timedelta(days=due_in_days)))
        task.update(fields)
        if "id" in fields:
            task["_id"] = task["id"] = fields["id"]
        if kind == "daily" and "isDue" not in fields:
            task["isDue"] = self._is_due(task)
        self.tasks[task["id"]] = task
        return task

    def fail_next(
        self,
        status: int,
        *,
        error: str = "Error",
        message: str = "Forced failure",
        headers: Mapping[str, str] | None = None,
        times: int = 1,
    ) -> None:
        body = {"success": False, "error": error, "message": message}
        self._failures.extend(
            ForcedFailure(status, body, dict(headers or {})) for _ in range(times)
        )

    def api_requests(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.path.startswith("/api/")]

    def state(self) -> dict[str, Any]:
        return {"user": copy.deepcopy(self.user), "tasks": copy.deepcopy(list(self.tasks.values()))}

    # --------------------------------------------------------------- calendar

    def habitica_day(self, instant: datetime) -> date:
        prefs = self.user["preferences"]
        local = instant.astimezone(UTC).replace(tzinfo=None) - timedelta(
            minutes=prefs.get("timezoneOffset", 0)
        )
        return (local - timedelta(hours=prefs.get("dayStart", 0))).date()

    def today(self) -> date:
        return self.habitica_day(self.now())

    def _local_midnight(self, day: date) -> datetime:
        offset = timedelta(minutes=self.user["preferences"].get("timezoneOffset", 0))
        return datetime(day.year, day.month, day.day, tzinfo=UTC) + offset

    def local_date(self, instant: datetime) -> date:
        offset = timedelta(minutes=self.user["preferences"].get("timezoneOffset", 0))
        return (instant.astimezone(UTC).replace(tzinfo=None) - offset).date()

    def _is_due(self, task: dict[str, Any]) -> bool:
        today = self.today()
        start_instant = _parse_instant(task.get("startDate"))
        start = self.local_date(start_instant) if start_instant else today
        if today < start:
            return False
        every = max(int(task.get("everyX") or 1), 1)
        frequency = task.get("frequency", "weekly")
        if frequency == "daily":
            return (today - start).days % every == 0
        if frequency == "weekly":
            weeks = ((today - start).days // 7) % every == 0
            return weeks and bool(task.get("repeat", {}).get(WEEKDAY_KEYS[today.weekday()], False))
        if frequency == "monthly":
            months = ((today.year - start.year) * 12 + today.month - start.month) % every == 0
            return months and today.day in task.get("daysOfMonth", [])
        if frequency == "yearly":
            return (today.year - start.year) % every == 0 and (today.month, today.day) == (
                start.month,
                start.day,
            )
        return True

    # ------------------------------------------------------------------ builders

    def _new_task(self, kind: str, text: str) -> dict[str, Any]:
        now = _iso(self.now())
        task_id = str(uuid.uuid4())
        task: dict[str, Any] = {
            "_id": task_id, "id": task_id, "userId": self.user_id, "type": kind, "text": text,
            "notes": "", "tags": [], "value": 0, "priority": 1, "attribute": "str",
            "challenge": {}, "group": {"assignedUsers": [], "assignedUsersDetail": {}},
            "reminders": [], "byHabitica": False, "createdAt": now, "updatedAt": now,
        }  # fmt: skip
        if kind == "habit":
            task.update(
                up=True, down=True, counterUp=0, counterDown=0, frequency="daily", history=[]
            )
        elif kind == "daily":
            task.update(
                completed=False, isDue=True, streak=0, frequency="weekly", everyX=1,
                repeat=dict.fromkeys(WEEKDAY_KEYS, True), startDate=now, daysOfMonth=[],
                weeksOfMonth=[], nextDue=[], yesterDaily=True, checklist=[],
                collapseChecklist=False, history=[],
            )  # fmt: skip
        elif kind == "todo":
            task.update(completed=False, checklist=[], collapseChecklist=False)
        else:
            task["value"] = 10
        return task

    def _checklist_item(self, item: Any) -> dict[str, Any]:
        if isinstance(item, str):
            return {"id": str(uuid.uuid4()), "text": item, "completed": False}
        return {
            "id": item.get("id") or str(uuid.uuid4()),
            "text": item["text"],
            "completed": bool(item.get("completed")),
        }

    def _tag_ref_to_id(self, ref: str) -> str:
        for tag in self.user["tags"]:
            if ref in (tag["id"], tag["name"]):
                return tag["id"]
        return self.add_tag(ref)["id"]

    # ------------------------------------------------------------------- serving

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._middleware])
        routes = web.RouteTableDef()
        api = "/api/v3"

        routes.get(f"{api}/user")(self._get_user)
        routes.get(f"{api}/tasks/user")(self._get_tasks)
        routes.post(f"{api}/tasks/user")(self._create_task)
        routes.get(f"{api}/tasks/{{task_id}}")(self._get_task)
        routes.put(f"{api}/tasks/{{task_id}}")(self._update_task)
        routes.delete(f"{api}/tasks/{{task_id}}")(self._delete_task)
        routes.post(f"{api}/tasks/{{task_id}}/score/{{direction}}")(self._score_task)
        routes.post(f"{api}/tasks/{{task_id}}/checklist")(self._add_checklist_item)
        routes.put(f"{api}/tasks/{{task_id}}/checklist/{{item_id}}")(self._update_checklist_item)
        routes.post(f"{api}/tasks/{{task_id}}/checklist/{{item_id}}/score")(
            self._score_checklist_item
        )
        routes.delete(f"{api}/tasks/{{task_id}}/checklist/{{item_id}}")(self._delete_checklist_item)
        routes.get(f"{api}/tags")(self._get_tags)
        routes.post(f"{api}/tags")(self._create_tag)
        routes.put(f"{api}/tags/{{tag_id}}")(self._update_tag)
        routes.delete(f"{api}/tags/{{tag_id}}")(self._delete_tag)
        routes.get("/__state")(self._control_state)
        routes.post("/__reset")(self._control_reset)
        routes.get("/__requests")(self._control_requests)
        app.add_routes(routes)
        return app

    @web.middleware
    async def _middleware(
        self, request: web.Request, handler: Callable[..., Any]
    ) -> web.StreamResponse:
        if not request.path.startswith("/api/"):
            return await handler(request)
        body = await request.json() if request.can_read_body else None
        self.requests.append(
            RecordedRequest(
                method=request.method,
                path=request.path,
                query=dict(request.query),
                body=body,
                headers={k.lower(): v for k, v in request.headers.items()},
            )
        )
        if not request.headers.get("x-client"):
            return _error(400, "BadRequest", 'Missing "x-client" header.')
        if not request.headers.get("x-api-user") or not request.headers.get("x-api-key"):
            return _error(401, "NotAuthorized", "Missing authentication headers.")
        if (request.headers["x-api-user"], request.headers["x-api-key"]) != (
            self.user_id,
            self.api_token,
        ):
            return _error(401, "NotAuthorized", "There is no account that uses those credentials.")

        limit_headers, exceeded, retry_after = self._consume_rate_limit()
        if exceeded:
            response = _error(429, "TooManyRequests", "Too many requests.")
            response.headers.update(limit_headers)
            response.headers["Retry-After"] = str(retry_after)
            return response
        if self._failures:
            failure = self._failures.pop(0)
            response = web.json_response(failure.body, status=failure.status)
            response.headers.update(limit_headers)
            response.headers.update(failure.headers)
            return response
        request[JSON_BODY] = body if isinstance(body, dict) else {}
        response = await handler(request)
        response.headers.update(limit_headers)
        return response

    def _consume_rate_limit(self) -> tuple[dict[str, str], bool, int]:
        now = self.now()
        if self._window_start is None or now - self._window_start >= timedelta(seconds=60):
            self._window_start, self._window_count = now, 0
        self._window_count += 1
        reset = self._window_start + timedelta(seconds=60)
        headers = {
            "X-RateLimit-Limit": str(self.rate_limit),
            "X-RateLimit-Remaining": str(max(self.rate_limit - self._window_count, 0)),
            "X-RateLimit-Reset": reset.strftime(
                "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)"
            ),
        }
        exceeded = self._window_count > self.rate_limit
        return headers, exceeded, max(int((reset - now).total_seconds()), 1)

    def _envelope(self, data: Any, status: int = 200) -> web.Response:
        return web.json_response(
            {
                "success": True,
                "data": data,
                "notifications": [],
                "userV": 1,
                "appVersion": APP_VERSION,
            },
            status=status,
        )

    def _find_task(self, reference: str) -> dict[str, Any] | None:
        if reference in self.tasks:
            return self.tasks[reference]
        return next((t for t in self.tasks.values() if t.get("alias") == reference), None)

    # ---------------------------------------------------------------- handlers

    async def _get_user(self, request: web.Request) -> web.Response:
        fields = request.query.get("userFields")
        if not fields:
            # Only the whole document carries the computed stats (Habitica's toJSON transform).
            full = copy.deepcopy(self.user)
            full["stats"].update(
                toNextLevel=_to_next_level(full["stats"]["lvl"]), maxHealth=50, maxMP=42
            )
            last_cron = _parse_instant(full.get("lastCron"))
            full["needsCron"] = (
                last_cron is not None and self.habitica_day(last_cron) < self.today()
            )
            return self._envelope(full)
        projected: dict[str, Any] = {"_id": self.user_id, "id": self.user_id}
        for path in (p.strip() for p in fields.split(",") if p.strip()):
            _copy_path(self.user, projected, path.split("."))
        return self._envelope(projected)

    async def _get_tasks(self, request: web.Request) -> web.Response:
        kind = request.query.get("type")
        valid = {"habits", "dailys", "todos", "rewards", "completedTodos"}
        if kind is not None and kind not in valid:
            return _error(400, "BadRequest", "Invalid request parameters.", [
                'Task type must be one of "habits", "dailys", "todos", "rewards", "completedTodos".'
            ])  # fmt: skip
        tasks = list(self.tasks.values())
        if kind == "completedTodos":
            done = [t for t in tasks if t["type"] == "todo" and t.get("completed")]
            done.sort(key=lambda t: t.get("dateCompleted", ""), reverse=True)
            selected = done[:30]
        else:
            selected = [t for t in tasks if not (t["type"] == "todo" and t.get("completed"))]
            if kind is not None:
                singular = {
                    "habits": "habit",
                    "dailys": "daily",
                    "todos": "todo",
                    "rewards": "reward",
                }[kind]
                selected = [t for t in selected if t["type"] == singular]
        include_history = request.query.get("history") != "false"
        out = []
        for task in selected:
            item = copy.deepcopy(task)
            if not include_history and "history" in item:
                item["history"] = []
            out.append(item)
        return self._envelope(out)

    async def _get_task(self, request: web.Request) -> web.Response:
        task = self._find_task(request.match_info["task_id"])
        if task is None:
            return _error(404, "NotFound", "Task not found.")
        return self._envelope(task)

    async def _create_task(self, request: web.Request) -> web.Response:
        body = request[JSON_BODY]
        kind = body.get("type")
        if kind not in TASK_TYPES:
            return _error(400, "BadRequest", "Invalid task type.")
        if not isinstance(body.get("text"), str) or not body["text"].strip():
            return _error(400, "BadRequest", "Task text cannot be empty.")
        task = self._new_task(kind, body["text"])
        for key, value in body.items():
            if key in UPDATABLE_FIELDS - {"text"}:
                task[key] = value
        if "checklist" in body:
            task["checklist"] = [self._checklist_item(item) for item in body["checklist"]]
        if kind == "daily":
            task["isDue"] = self._is_due(task)
        self.tasks[task["id"]] = task
        return self._envelope(task, status=201)

    async def _update_task(self, request: web.Request) -> web.Response:
        task = self._find_task(request.match_info["task_id"])
        if task is None:
            return _error(404, "NotFound", "Task not found.")
        body = request[JSON_BODY]
        allowed = CHALLENGE_UPDATABLE_FIELDS if task["challenge"].get("id") else UPDATABLE_FIELDS
        for key, value in body.items():
            if key not in allowed:
                continue
            if key == "checklist":
                task[key] = [self._checklist_item(item) for item in value]
            elif value is None:
                task.pop(key, None)
            else:
                task[key] = value
        task["updatedAt"] = _iso(self.now())
        if task["type"] == "daily":
            task["isDue"] = self._is_due(task)
        return self._envelope(task)

    async def _delete_task(self, request: web.Request) -> web.Response:
        task = self._find_task(request.match_info["task_id"])
        if task is None:
            return _error(404, "NotFound", "Task not found.")
        challenge = task.get("challenge", {})
        if challenge.get("id") and not challenge.get("broken"):
            return _error(401, "NotAuthorized", "A task belonging to a challenge can't be deleted.")
        del self.tasks[task["id"]]
        return self._envelope({})

    async def _score_task(self, request: web.Request) -> web.Response:
        task = self._find_task(request.match_info["task_id"])
        direction = request.match_info["direction"]
        if direction not in ("up", "down"):
            return _error(400, "BadRequest", "Invalid request parameters.")
        if task is None:
            return _error(404, "NotFound", "Task not found.")
        stats = self.user["stats"]
        up = direction == "up"
        points = PRIORITY_REWARD.get(float(task.get("priority", 1)), 2)
        delta = 1.0 if up else -1.0
        if task["type"] == "reward":
            if task["value"] > stats["gp"]:
                return _error(401, "NotAuthorized", "Not Enough Gold")
            stats["gp"] = round(stats["gp"] - task["value"], 2)
            delta = 0.0
        elif task["type"] in ("daily", "todo"):
            if up == bool(task.get("completed")):
                return _error(
                    401, "NotAuthorized", "Your session is outdated. Please refresh or sync."
                )
            sign = 1 if up else -1
            task["completed"] = up
            if task["type"] == "todo":
                if up:
                    task["dateCompleted"] = _iso(self.now())
                else:
                    task.pop("dateCompleted", None)
            else:
                task["streak"] = max(task.get("streak", 0) + sign, 0)
            stats["exp"] += sign * 5 * points
            stats["gp"] = round(stats["gp"] + sign * points, 2)
            stats["mp"] = round(stats["mp"] + sign * 1.0, 2)
        elif up:
            task["counterUp"] = task.get("counterUp", 0) + 1
            stats["exp"] += 3 * points
            stats["gp"] = round(stats["gp"] + 0.5 * points, 2)
        else:
            task["counterDown"] = task.get("counterDown", 0) + 1
            stats["hp"] = round(stats["hp"] - 2.0 * points, 2)
        while stats["exp"] >= _to_next_level(stats["lvl"]):
            stats["exp"] -= _to_next_level(stats["lvl"])
            stats["lvl"] += 1
        tmp: dict[str, Any] = {}
        if self.next_drop is not None and up:
            tmp["drop"] = self.next_drop
            self.next_drop = None
        return self._envelope({**copy.deepcopy(stats), "delta": delta, "_tmp": tmp})

    def _checklist_task(
        self, request: web.Request
    ) -> tuple[dict[str, Any] | None, web.Response | None]:
        task = self._find_task(request.match_info["task_id"])
        if task is None:
            return None, _error(404, "NotFound", "Task not found.")
        if task["type"] not in ("daily", "todo"):
            return None, _error(
                400, "BadRequest", "Checklists are supported only on Dailies and To Do's"
            )
        return task, None

    def _checklist_entry(self, task: dict[str, Any], item_id: str) -> dict[str, Any] | None:
        return next((i for i in task["checklist"] if i["id"] == item_id), None)

    async def _add_checklist_item(self, request: web.Request) -> web.Response:
        task, problem = self._checklist_task(request)
        if problem is not None or task is None:
            return problem or _error(500, "Error", "unreachable")
        body = request[JSON_BODY]
        task["checklist"].append(
            {"id": str(uuid.uuid4()), "text": body.get("text", ""), "completed": False}
        )
        return self._envelope(task)

    async def _update_checklist_item(self, request: web.Request) -> web.Response:
        task, problem = self._checklist_task(request)
        if problem is not None or task is None:
            return problem or _error(500, "Error", "unreachable")
        entry = self._checklist_entry(task, request.match_info["item_id"])
        if entry is None:
            return _error(404, "NotFound", "Checklist item not found.")
        entry["text"] = (request[JSON_BODY]).get("text", entry["text"])
        return self._envelope(task)

    async def _score_checklist_item(self, request: web.Request) -> web.Response:
        task, problem = self._checklist_task(request)
        if problem is not None or task is None:
            return problem or _error(500, "Error", "unreachable")
        entry = self._checklist_entry(task, request.match_info["item_id"])
        if entry is None:
            return _error(404, "NotFound", "Checklist item not found.")
        entry["completed"] = not entry["completed"]
        return self._envelope(task)

    async def _delete_checklist_item(self, request: web.Request) -> web.Response:
        task, problem = self._checklist_task(request)
        if problem is not None or task is None:
            return problem or _error(500, "Error", "unreachable")
        entry = self._checklist_entry(task, request.match_info["item_id"])
        if entry is None:
            return _error(404, "NotFound", "Checklist item not found.")
        task["checklist"].remove(entry)
        return self._envelope(task)

    async def _get_tags(self, _request: web.Request) -> web.Response:
        return self._envelope(self.user["tags"])

    async def _create_tag(self, request: web.Request) -> web.Response:
        name = (request[JSON_BODY]).get("name")
        if not isinstance(name, str) or not name:
            return _error(400, "BadRequest", "Invalid request parameters.")
        return self._envelope(self.add_tag(name), status=201)

    async def _update_tag(self, request: web.Request) -> web.Response:
        tag = next((t for t in self.user["tags"] if t["id"] == request.match_info["tag_id"]), None)
        if tag is None:
            return _error(404, "NotFound", "Tag not found.")
        tag["name"] = (request[JSON_BODY]).get("name", tag["name"])
        return self._envelope(tag)

    async def _delete_tag(self, request: web.Request) -> web.Response:
        tag_id = request.match_info["tag_id"]
        tag = next((t for t in self.user["tags"] if t["id"] == tag_id), None)
        if tag is None:
            return _error(404, "NotFound", "Tag not found.")
        self.user["tags"].remove(tag)
        for task in self.tasks.values():
            if tag_id in task["tags"]:
                task["tags"].remove(tag_id)
        return self._envelope({})

    async def _control_state(self, _request: web.Request) -> web.Response:
        return web.json_response(self.state())

    async def _control_reset(self, request: web.Request) -> web.Response:
        seed = await request.json() if request.can_read_body else None
        self.reset(seed)
        return web.json_response({"ok": True})

    async def _control_requests(self, _request: web.Request) -> web.Response:
        return web.json_response(
            [
                {"method": r.method, "path": r.path, "query": r.query, "body": r.body}
                for r in self.api_requests()
            ]
        )


def _to_next_level(level: int) -> int:
    if level < 5:
        return 25 * level
    if level == 5:
        return 150
    return round(((level**2) * 0.25 + 10 * level + 139.75) / 10) * 10


def _error(status: int, error: str, message: str, details: list[str] | None = None) -> web.Response:
    body: dict[str, Any] = {"success": False, "error": error, "message": message}
    if details:
        body["errors"] = [{"message": text} for text in details]
    return web.json_response(body, status=status)


def _deep_merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _copy_path(source: Mapping[str, Any], target: dict[str, Any], path: list[str]) -> None:
    head, *rest = path
    if head not in source:
        return
    if not rest:
        target[head] = copy.deepcopy(source[head])
        return
    if isinstance(source[head], Mapping):
        _copy_path(source[head], target.setdefault(head, {}), rest)
