"""Task tools: list, get, create, update, score and delete."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from habiticalib import Direction, TagsUser, TaskData, TaskFilter, UserData
from pydantic import Field

from ..const import DEFAULT_LIST_LIMIT, MAX_HEALTH, MAX_LIST_LIMIT
from ..cron import needs_cron
from ..dates import UserClock
from ..errors import ToolInputError
from ..gateway import CLOCK_FIELDS, CRON_FIELDS, HabiticaGateway
from ..models import (
    DailySchedule,
    Difficulty,
    PlayerStats,
    ScoreResult,
    StatChanges,
    TaskList,
    TaskResult,
    TaskType,
    TaskView,
)
from ..projections import (
    PRIORITY_BY_DIFFICULTY,
    REPEAT_KEY_BY_WEEKDAY,
    player_stats,
    tag_names_by_id,
    task_type_name,
    task_view,
    to_next_level,
    todo_due_date,
)
from ..resolve import find_tag, find_task, normalize, resolve_tag_ids
from ._common import (
    ADDITIVE,
    CRON_WARNING,
    DESTRUCTIVE,
    IDEMPOTENT_UPDATE,
    READ_ONLY,
    READ_TAG,
    WRITE_TAG,
    Confirm,
    Format,
    TaskReference,
    gateway_for,
)

ListStatus = Literal["all", "pending", "due_today", "overdue", "completed"]
SCORE_USER_FIELDS = ("stats", "tags", *CLOCK_FIELDS, *CRON_FIELDS)
DEFAULT_REWARD_COST = 10.0

# ----------------------------------------------------------------------- list / get


def _matches_status(task: TaskData, status: ListStatus, clock: UserClock) -> bool:
    kind = task_type_name(task)
    if status == "all":
        return not (kind == "todo" and task.completed)
    if status == "completed":
        return kind in ("daily", "todo") and bool(task.completed)
    if status == "pending":
        if kind == "daily":
            return bool(task.isDue) and not task.completed
        return kind == "todo" and not task.completed
    if kind == "daily":
        return status == "due_today" and bool(task.isDue)
    if kind != "todo" or task.completed:
        return False
    due = todo_due_date(task, clock)
    if due is None:
        return False
    return due == clock.today() if status == "due_today" else due < clock.today()


async def list_tasks(
    gateway: HabiticaGateway,
    *,
    task_type: TaskType | None = None,
    status: ListStatus = "all",
    search: str | None = None,
    tag: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    detailed: bool = False,
) -> TaskList:
    tasks = list(await gateway.get_tasks())
    if status == "completed" and task_type in (None, "todo"):
        tasks.extend(await gateway.get_tasks(TaskFilter.COMPLETED_TODOS))
    clock, tags = await gateway.get_context()

    if task_type is not None:
        tasks = [t for t in tasks if task_type_name(t) == task_type]
    if tag:
        found = find_tag(tags, tag)
        if found is None:
            raise ToolInputError(_unknown_tag_message(tag, tags))
        tasks = [t for t in tasks if found.id in t.tags]
    if search and (key := normalize(search)):
        tasks = [
            t for t in tasks if key in normalize(t.text or "") or key in normalize(t.notes or "")
        ]
    tasks = [t for t in tasks if _matches_status(t, status, clock)]

    total = len(tasks)
    note = None
    if total == 0:
        note = "No tasks match these filters."
    elif total > limit:
        note = (
            f"Showing {limit} of {total}. Narrow with task_type, status, tag or search, or "
            f"raise limit (max {MAX_LIST_LIMIT})."
        )
    names = tag_names_by_id(tags)
    return TaskList(
        tasks=[
            task_view(t, clock=clock, tag_names=names, detailed=detailed) for t in tasks[:limit]
        ],
        total_matching=total,
        truncated=total > limit,
        note=note,
    )


async def get_task(gateway: HabiticaGateway, reference: str) -> TaskView:
    task = await find_task(gateway, reference, include_completed_todos=True)
    clock, tags = await gateway.get_context()
    return task_view(task, clock=clock, tag_names=tag_names_by_id(tags), detailed=True)


# ------------------------------------------------------------------ create / update


def _check_fields_for_type(
    kind: TaskType,
    *,
    due_date: bool = False,
    checklist: bool = False,
    schedule: bool = False,
    habit_buttons: bool = False,
    cost: bool = False,
) -> None:
    problems: list[str] = []
    if due_date and kind != "todo":
        problems.append("due dates only apply to to-dos (dailies use schedule)")
    if checklist and kind not in ("daily", "todo"):
        problems.append("checklists only exist on dailies and to-dos")
    if schedule and kind != "daily":
        problems.append("schedule only applies to dailies")
    if habit_buttons and kind != "habit":
        problems.append("habit_positive / habit_negative only apply to habits")
    if cost and kind != "reward":
        problems.append("cost only applies to rewards")
    if problems:
        raise ToolInputError(f"Invalid fields for a {kind}: " + "; ".join(problems) + ".")


def schedule_body(schedule: DailySchedule, clock: UserClock) -> dict[str, Any]:
    """Translate a :class:`DailySchedule` into Habitica's daily fields."""
    if schedule.weekdays is not None and schedule.frequency != "weekly":
        raise ToolInputError("weekdays only apply when frequency is 'weekly'.")
    if schedule.days_of_month is not None and schedule.frequency != "monthly":
        raise ToolInputError("days_of_month only apply when frequency is 'monthly'.")
    start = schedule.start_date or clock.today()
    body: dict[str, Any] = {"frequency": schedule.frequency, "everyX": schedule.every_x}
    if schedule.frequency == "weekly":
        days = set(REPEAT_KEY_BY_WEEKDAY) if schedule.weekdays is None else set(schedule.weekdays)
        if not days:
            raise ToolInputError("weekdays cannot be empty; omit it to mean every day.")
        body["repeat"] = {key: day in days for day, key in REPEAT_KEY_BY_WEEKDAY.items()}
    elif schedule.frequency == "monthly":
        body["daysOfMonth"] = sorted(set(schedule.days_of_month or [start.day]))
        body["weeksOfMonth"] = []
    body["startDate"] = clock.local_midnight(start).isoformat()
    return body


def _clean_checklist(items: Sequence[str]) -> list[dict[str, Any]]:
    return [{"text": text.strip(), "completed": False} for text in items if text.strip()]


async def create_task(
    gateway: HabiticaGateway,
    *,
    task_type: TaskType,
    text: str,
    notes: str | None = None,
    difficulty: Difficulty = "easy",
    due_date: date | None = None,
    checklist: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    create_missing_tags: bool = True,
    schedule: DailySchedule | None = None,
    habit_positive: bool | None = None,
    habit_negative: bool | None = None,
    cost: float | None = None,
) -> TaskResult:
    text = text.strip()
    if not text:
        raise ToolInputError("text cannot be empty.")
    _check_fields_for_type(
        task_type,
        due_date=due_date is not None,
        checklist=bool(checklist),
        schedule=schedule is not None,
        habit_buttons=habit_positive is not None or habit_negative is not None,
        cost=cost is not None,
    )
    clock, known_tags = await gateway.get_context()
    tag_ids, created_tags = await resolve_tag_ids(
        gateway, known_tags, tags or [], create_missing=create_missing_tags
    )

    body: dict[str, Any] = {"type": task_type, "text": text}
    if task_type != "reward":
        body["priority"] = PRIORITY_BY_DIFFICULTY[difficulty]
    if notes:
        body["notes"] = notes
    if tag_ids:
        body["tags"] = tag_ids
    if checklist and (items := _clean_checklist(checklist)):
        body["checklist"] = items
    if due_date is not None:
        body["date"] = clock.local_midnight(due_date).isoformat()
    if schedule is not None:
        body.update(schedule_body(schedule, clock))
    if task_type == "habit":
        body["up"] = True if habit_positive is None else habit_positive
        body["down"] = False if habit_negative is None else habit_negative
    if task_type == "reward":
        body["value"] = DEFAULT_REWARD_COST if cost is None else cost

    task = await gateway.create_task(body)
    names = tag_names_by_id([*known_tags, *created_tags])
    return TaskResult(
        status="done",
        message=f"Created {task_type} '{task.text}'.",
        task=task_view(task, clock=clock, tag_names=names, detailed=True),
        created_tags=[t.name or "" for t in created_tags],
    )


async def update_task(
    gateway: HabiticaGateway,
    *,
    reference: str,
    text: str | None = None,
    notes: str | None = None,
    difficulty: Difficulty | None = None,
    due_date: date | None = None,
    clear_due_date: bool = False,
    schedule: DailySchedule | None = None,
    habit_positive: bool | None = None,
    habit_negative: bool | None = None,
    cost: float | None = None,
    add_tags: Sequence[str] | None = None,
    remove_tags: Sequence[str] | None = None,
    create_missing_tags: bool = True,
) -> TaskResult:
    task = await find_task(gateway, reference)
    kind = task_type_name(task)
    if due_date is not None and clear_due_date:
        raise ToolInputError("Use either due_date or clear_due_date, not both.")
    _check_fields_for_type(
        kind,
        due_date=due_date is not None or clear_due_date,
        schedule=schedule is not None,
        habit_buttons=habit_positive is not None or habit_negative is not None,
        cost=cost is not None,
    )
    locked = (text, difficulty, due_date, schedule, habit_positive, habit_negative, cost)
    if _in_active_challenge(task) and (clear_due_date or any(v is not None for v in locked)):
        raise ToolInputError(
            f"'{task.text}' belongs to the challenge '{task.challenge.shortName}': only its "
            "notes, tags and checklist can be changed."
        )

    clock, known_tags = await gateway.get_context()
    body: dict[str, Any] = {}
    if text is not None:
        if not text.strip():
            raise ToolInputError("text cannot be empty.")
        if text.strip() != task.text:
            body["text"] = text.strip()
    if notes is not None and notes != (task.notes or ""):
        body["notes"] = notes
    if difficulty is not None and kind != "reward":
        priority = PRIORITY_BY_DIFFICULTY[difficulty]
        if task.priority is None or float(task.priority.value) != priority:
            body["priority"] = priority
    if due_date is not None:
        body["date"] = clock.local_midnight(due_date).isoformat()
    if clear_due_date and task.date is not None:
        body["date"] = None
    if schedule is not None:
        body.update(schedule_body(schedule, clock))
    if habit_positive is not None and habit_positive != task.up:
        body["up"] = habit_positive
    if habit_negative is not None and habit_negative != task.down:
        body["down"] = habit_negative
    if cost is not None and cost != task.value:
        body["value"] = cost

    created_tags: list[TagsUser] = []
    if add_tags or remove_tags:
        current = [str(tag_id) for tag_id in task.tags]
        add_ids, created_tags = await resolve_tag_ids(
            gateway, known_tags, add_tags or [], create_missing=create_missing_tags
        )
        remove_ids: set[str] = set()
        for reference_tag in remove_tags or []:
            found = find_tag(known_tags, reference_tag)
            if found is None:
                raise ToolInputError(_unknown_tag_message(reference_tag, known_tags))
            remove_ids.add(str(found.id))
        new_tags = [t for t in current if t not in remove_ids]
        new_tags += [t for t in add_ids if t not in new_tags and t not in remove_ids]
        if new_tags != current:
            body["tags"] = new_tags

    names = tag_names_by_id([*known_tags, *created_tags])
    if not body:
        return TaskResult(
            status="no_change",
            message="Nothing to change: no field was given or the task already has these values.",
            task=task_view(task, clock=clock, tag_names=names),
        )
    updated = await gateway.update_task(str(task.id), body)
    return TaskResult(
        status="done",
        message=f"Updated {kind} '{updated.text}'.",
        task=task_view(updated, clock=clock, tag_names=names, detailed=True),
        created_tags=[t.name or "" for t in created_tags],
    )


# ---------------------------------------------------------------------------- score


def _in_active_challenge(task: TaskData) -> bool:
    return task.challenge.id is not None and not task.challenge.broken


def _number(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _stats_after(user: UserData, payload: dict[str, Any]) -> PlayerStats:
    level = _number(payload, "lvl")
    max_hp = _number(payload, "maxHealth")
    max_mp = _number(payload, "maxMP")
    to_next = _number(payload, "toNextLevel")
    character_class = payload.get("class")
    new_level = int(level) if level is not None else user.stats.lvl
    return PlayerStats(
        level=new_level,
        character_class=character_class if isinstance(character_class, str) else None,
        hp=_rounded(_number(payload, "hp")),
        max_hp=int(max_hp) if max_hp is not None else (user.stats.maxHealth or MAX_HEALTH),
        mp=_rounded(_number(payload, "mp")),
        max_mp=int(max_mp) if max_mp is not None else user.stats.maxMP,
        exp=_rounded(_number(payload, "exp")),
        exp_to_next_level=int(to_next) if to_next is not None else to_next_level(new_level),
        gold=_rounded(_number(payload, "gp")),
    )


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _difference(after: float | None, before: float | None) -> float | None:
    if after is None or before is None:
        return None
    change = round(after - before, 2)
    return change or None


def _drop_message(payload: dict[str, Any]) -> str | None:
    tmp = payload.get("_tmp")
    if not isinstance(tmp, dict):
        return None
    drop = tmp.get("drop")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(drop, dict):
        return None
    dialog = drop.get("dialog")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return dialog if isinstance(dialog, str) and dialog else None


def _task_after_score(task: TaskData, kind: TaskType, direction: Direction) -> TaskData:
    up = direction is Direction.UP
    if kind == "todo":
        return dataclasses.replace(task, completed=up)
    if kind == "daily":
        streak = max((task.streak or 0) + (1 if up else -1), 0)
        return dataclasses.replace(task, completed=up, streak=streak)
    if kind == "habit":
        if up:
            return dataclasses.replace(task, counterUp=(task.counterUp or 0) + 1)
        return dataclasses.replace(task, counterDown=(task.counterDown or 0) + 1)
    return task


_SCORE_MESSAGES: dict[tuple[TaskType, Direction], str] = {
    ("todo", Direction.UP): "Completed the to-do '{text}'.",
    ("todo", Direction.DOWN): "Marked the to-do '{text}' as not completed.",
    ("daily", Direction.UP): "Checked off the daily '{text}'.",
    ("daily", Direction.DOWN): "Unchecked the daily '{text}'.",
    ("habit", Direction.UP): "Scored + on the habit '{text}'.",
    ("habit", Direction.DOWN): "Scored - on the habit '{text}'.",
    ("reward", Direction.UP): "Bought the reward '{text}'.",
}


async def score_task(
    gateway: HabiticaGateway,
    *,
    reference: str,
    direction: Literal["up", "down"] = "up",
    confirm: bool = False,
) -> ScoreResult:
    way = Direction(direction)
    task = await find_task(gateway, reference, include_completed_todos=way is Direction.DOWN)
    kind = task_type_name(task)
    user = await gateway.get_user(SCORE_USER_FIELDS)
    clock = gateway.clock_for(user)
    names = tag_names_by_id(user.tags)

    def view(item: TaskData) -> TaskView:
        return task_view(item, clock=clock, tag_names=names)

    warnings: list[str] = []
    if kind == "reward":
        if way is Direction.DOWN:
            raise ToolInputError("Rewards can only be bought (direction='up').")
        gold, price = user.stats.gp or 0.0, task.value or 0.0
        if price > gold:
            raise ToolInputError(
                f"Not enough gold: '{task.text}' costs {price:g} and the user has {gold:.2f}."
            )
        if not confirm:
            return ScoreResult(
                status="confirmation_required",
                message=f"Buying '{task.text}' costs {price:g} gold (the user has {gold:.2f}). "
                f"Ask the user to confirm, then call habitica_score_task again with "
                f"task='{task.id}' and confirm=true.",
                task=view(task),
                stats=player_stats(user),
            )
    elif kind == "habit":
        enabled = task.up if way is Direction.UP else task.down
        if enabled is False:
            sign, field = (
                ("+", "habit_positive") if way is Direction.UP else ("-", "habit_negative")
            )
            raise ToolInputError(
                f"The habit '{task.text}' has no {sign} button. Score the other direction or "
                f"enable it with habitica_update_task({field}=true)."
            )
    else:
        if way is Direction.UP and task.completed:
            return ScoreResult(
                status="no_change", message=f"'{task.text}' is already completed.", task=view(task)
            )
        if way is Direction.DOWN and not task.completed:
            return ScoreResult(
                status="no_change",
                message=f"'{task.text}' is not completed, so there is nothing to undo.",
                task=view(task),
            )
        if kind == "daily":
            if await needs_cron(gateway, user, clock):
                warnings.append(CRON_WARNING)
            if way is Direction.UP and not task.isDue:
                warnings.append(f"'{task.text}' is not due today.")

    payload = await gateway.score_task(str(task.id), way)
    after = _stats_after(user, payload)
    before = user.stats
    same_level = after.level == before.lvl
    changes = StatChanges(
        hp=_difference(after.hp, before.hp),
        mp=_difference(after.mp, before.mp),
        exp=_difference(after.exp, before.exp) if same_level else None,
        gold=_difference(after.gold, before.gp),
    )
    return ScoreResult(
        status="done",
        message=_SCORE_MESSAGES[(kind, way)].format(text=task.text),
        task=view(_task_after_score(task, kind, way)),
        stats=after,
        changes=changes if changes.model_dump(exclude_none=True) else None,
        level_up=True if (after.level or 0) > (before.lvl or 0) else None,
        drop=_drop_message(payload),
        warnings=warnings,
    )


# --------------------------------------------------------------------------- delete


async def delete_task(
    gateway: HabiticaGateway, *, reference: str, confirm: bool = False
) -> TaskResult:
    task = await find_task(gateway, reference, include_completed_todos=True)
    kind = task_type_name(task)
    clock, tags = await gateway.get_context()
    view = task_view(task, clock=clock, tag_names=tag_names_by_id(tags), detailed=True)
    if _in_active_challenge(task):
        raise ToolInputError(
            f"'{task.text}' belongs to the challenge '{task.challenge.shortName}' and Habitica "
            "does not allow deleting challenge tasks. The user can leave the challenge in "
            "Habitica and choose to remove its tasks."
        )
    if not confirm:
        return TaskResult(
            status="confirmation_required",
            message=f"This permanently deletes the {kind} '{task.text}'. Ask the user to "
            f"confirm, then call habitica_delete_task again with task='{task.id}' and "
            "confirm=true.",
            task=view,
        )
    await gateway.delete_task(str(task.id))
    return TaskResult(status="done", message=f"Deleted the {kind} '{task.text}'.", task=view)


def _unknown_tag_message(reference: str, tags: Sequence[TagsUser]) -> str:
    available = ", ".join(sorted(t.name or "" for t in tags)) or "none"
    return f"Tag {reference!r} does not exist. Existing tags: {available}."


# ---------------------------------------------------------------------- registration

TagNames = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=100)]] | None,
    Field(description="Tag names (or ids)"),
]


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="habitica_list_tasks",
        title="Habitica: list tasks",
        annotations=READ_ONLY,
        tags={READ_TAG},
    )
    async def habitica_list_tasks(
        ctx: Context,
        task_type: Annotated[TaskType | None, Field(description="Only this type of task")] = None,
        status: Annotated[
            ListStatus,
            Field(
                description="'all' = active tasks; 'pending' = dailies due today and to-dos not "
                "done; 'due_today' = dailies due today and to-dos due today; 'overdue' = to-dos "
                "past their due date; 'completed' = dailies done today and the 30 most recently "
                "completed to-dos"
            ),
        ] = "all",
        search: Annotated[
            str | None,
            Field(max_length=200, description="Text contained in the task text or notes"),
        ] = None,
        tag: Annotated[str | None, Field(max_length=100, description="Tag name or id")] = None,
        limit: Annotated[int, Field(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
        response_format: Format = "concise",
    ) -> TaskList:
        """List the user's tasks with filters. Prefer filters over listing everything;
        returned ids can be passed to the other habitica_* tools."""
        return await list_tasks(
            await gateway_for(ctx),
            task_type=task_type,
            status=status,
            search=search,
            tag=tag,
            limit=limit,
            detailed=response_format == "detailed",
        )

    @mcp.tool(
        name="habitica_get_task",
        title="Habitica: get a task",
        annotations=READ_ONLY,
        tags={READ_TAG},
    )
    async def habitica_get_task(ctx: Context, task: TaskReference) -> TaskView:
        """Full details of one task: notes, checklist items with ids, schedule, tags and dates."""
        return await get_task(await gateway_for(ctx), task)

    @mcp.tool(
        name="habitica_create_task",
        title="Habitica: create a task",
        annotations=ADDITIVE,
        tags={WRITE_TAG},
    )
    async def habitica_create_task(
        ctx: Context,
        task_type: Annotated[
            TaskType,
            Field(
                description="'habit' = repeatable good/bad behavior scored with +/-; 'daily' = "
                "recurring task on a schedule; 'todo' = one-off task; 'reward' = custom reward "
                "bought with gold"
            ),
        ],
        text: Annotated[str, Field(min_length=1, max_length=500, description="Task title")],
        notes: Annotated[str | None, Field(max_length=2000)] = None,
        difficulty: Annotated[
            Difficulty, Field(description="Affects rewards and damage. Not used for rewards")
        ] = "easy",
        due_date: Annotated[
            date | None,
            Field(description="To-dos only: due date (YYYY-MM-DD) in the user's calendar"),
        ] = None,
        checklist: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=300)]] | None,
            Field(description="Dailies and to-dos only: checklist item texts"),
        ] = None,
        tags: TagNames = None,
        create_missing_tags: Annotated[
            bool, Field(description="Create tags that do not exist yet")
        ] = True,
        schedule: Annotated[
            DailySchedule | None,
            Field(description="Dailies only. Default: due every day starting today"),
        ] = None,
        habit_positive: Annotated[
            bool | None, Field(description="Habits only: enable + (default true)")
        ] = None,
        habit_negative: Annotated[
            bool | None,
            Field(description="Habits only: enable - (default false; set true for bad habits)"),
        ] = None,
        cost: Annotated[
            float | None,
            Field(ge=0, le=10000, description="Rewards only: price in gold (default 10)"),
        ] = None,
    ) -> TaskResult:
        """Create a habit, daily, to-do or reward. Tags are given by name."""
        return await create_task(
            await gateway_for(ctx),
            task_type=task_type,
            text=text,
            notes=notes,
            difficulty=difficulty,
            due_date=due_date,
            checklist=checklist,
            tags=tags,
            create_missing_tags=create_missing_tags,
            schedule=schedule,
            habit_positive=habit_positive,
            habit_negative=habit_negative,
            cost=cost,
        )

    @mcp.tool(
        name="habitica_update_task",
        title="Habitica: update a task",
        annotations=IDEMPOTENT_UPDATE,
        tags={WRITE_TAG},
    )
    async def habitica_update_task(
        ctx: Context,
        task: TaskReference,
        text: Annotated[str | None, Field(min_length=1, max_length=500)] = None,
        notes: Annotated[
            str | None, Field(max_length=2000, description="New notes ('' clears)")
        ] = None,
        difficulty: Difficulty | None = None,
        due_date: Annotated[date | None, Field(description="To-dos only: new due date")] = None,
        clear_due_date: Annotated[
            bool, Field(description="To-dos only: remove the due date")
        ] = False,
        schedule: Annotated[
            DailySchedule | None, Field(description="Dailies only: replaces the schedule")
        ] = None,
        habit_positive: Annotated[bool | None, Field(description="Habits only: enable +")] = None,
        habit_negative: Annotated[bool | None, Field(description="Habits only: enable -")] = None,
        cost: Annotated[float | None, Field(ge=0, le=10000, description="Rewards only")] = None,
        add_tags: TagNames = None,
        remove_tags: TagNames = None,
        create_missing_tags: bool = True,
    ) -> TaskResult:
        """Change fields of a task; only the given fields change. To check tasks off use
        habitica_score_task, and habitica_manage_checklist for checklist items."""
        return await update_task(
            await gateway_for(ctx),
            reference=task,
            text=text,
            notes=notes,
            difficulty=difficulty,
            due_date=due_date,
            clear_due_date=clear_due_date,
            schedule=schedule,
            habit_positive=habit_positive,
            habit_negative=habit_negative,
            cost=cost,
            add_tags=add_tags,
            remove_tags=remove_tags,
            create_missing_tags=create_missing_tags,
        )

    @mcp.tool(
        name="habitica_score_task",
        title="Habitica: check off / score a task",
        annotations=ADDITIVE,
        tags={WRITE_TAG},
    )
    async def habitica_score_task(
        ctx: Context,
        task: TaskReference,
        direction: Annotated[
            Literal["up", "down"],
            Field(
                description="'up' = complete a daily/to-do, + on a habit, or buy a reward; "
                "'down' = undo a completed daily/to-do, or - on a habit"
            ),
        ] = "up",
        confirm: Annotated[
            bool,
            Field(description="Rewards only: true after the user agreed to spend the gold"),
        ] = False,
    ) -> ScoreResult:
        """Record progress: check off or undo a daily or to-do, press + or - on a habit, or buy
        a custom reward. Returns the character's stats after scoring and what changed."""
        return await score_task(
            await gateway_for(ctx), reference=task, direction=direction, confirm=confirm
        )

    @mcp.tool(
        name="habitica_delete_task",
        title="Habitica: delete a task",
        annotations=DESTRUCTIVE,
        tags={WRITE_TAG},
    )
    async def habitica_delete_task(
        ctx: Context, task: TaskReference, confirm: Confirm = False
    ) -> TaskResult:
        """Permanently delete a task. Without confirm=true it only returns a preview: show it to
        the user and delete only after they agree."""
        return await delete_task(await gateway_for(ctx), reference=task, confirm=confirm)
