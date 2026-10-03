"""Turn habiticalib models into the compact views returned to the model.

Habitica documents are large (history, reminders, group data...). Only what helps an
LLM act on a task is kept, and ``concise`` views drop long notes and nested lists.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from habiticalib import Frequency as HabiticaFrequency
from habiticalib import TagsUser, TaskData, TaskPriority, TaskType, UserData

from .const import CONCISE_NOTES_CHARS, MAX_HEALTH
from .dates import UserClock
from .models import (
    ChecklistItemView,
    Difficulty,
    PlayerStats,
    ScheduleView,
    TagView,
    TaskView,
    Weekday,
)
from .models import TaskType as TaskTypeName

DIFFICULTY_BY_PRIORITY: Mapping[TaskPriority, Difficulty] = {
    TaskPriority.TRIVIAL: "trivial",
    TaskPriority.EASY: "easy",
    TaskPriority.MEDIUM: "medium",
    TaskPriority.HARD: "hard",
}
PRIORITY_BY_DIFFICULTY: Mapping[Difficulty, float] = {
    "trivial": 0.1,
    "easy": 1,
    "medium": 1.5,
    "hard": 2,
}
# Habitica's ``repeat`` object uses these keys for weekdays.
REPEAT_KEY_BY_WEEKDAY: Mapping[Weekday, str] = {
    "mon": "m",
    "tue": "t",
    "wed": "w",
    "thu": "th",
    "fri": "f",
    "sat": "s",
    "sun": "su",
}
MAX_NEXT_DUE = 3


def task_type_name(task: TaskData) -> TaskTypeName:
    return task.Type.value if task.Type is not None else "todo"  # type: ignore[return-value]


def tag_names_by_id(tags: Sequence[TagsUser]) -> dict[str, str]:
    return {str(tag.id): tag.name or str(tag.id) for tag in tags if tag.id is not None}


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def todo_due_date(task: TaskData, clock: UserClock) -> date | None:
    if task.Type is not TaskType.TODO or task.date is None:
        return None
    return clock.local_date(task.date)


def task_view(
    task: TaskData,
    *,
    clock: UserClock,
    tag_names: Mapping[str, str],
    detailed: bool = False,
) -> TaskView:
    """Build the view of one task; ``detailed`` adds notes, checklist and schedule."""
    kind = task_type_name(task)
    fields: dict[str, Any] = {
        "id": str(task.id) if task.id is not None else "",
        "type": kind,
        "text": task.text or "",
        "tags": [tag_names.get(str(tag_id), str(tag_id)) for tag_id in task.tags],
    }
    if task.notes:
        fields["notes"] = task.notes if detailed else truncate(task.notes, CONCISE_NOTES_CHARS)
    if task.priority is not None and kind != "reward":
        fields["difficulty"] = DIFFICULTY_BY_PRIORITY.get(task.priority)
    if task.challenge.id is not None:
        name = task.challenge.shortName or "a challenge"
        fields["challenge"] = f"{name} (ended)" if task.challenge.broken else name

    if kind in ("daily", "todo"):
        fields["completed"] = bool(task.completed)
        if task.checklist:
            done = sum(1 for item in task.checklist if item.completed)
            fields["checklist_progress"] = f"{done}/{len(task.checklist)}"
            if detailed:
                fields["checklist"] = [
                    ChecklistItemView(id=str(item.id), text=item.text, completed=item.completed)
                    for item in task.checklist
                ]
    if kind == "daily":
        fields["due_today"] = bool(task.isDue)
        fields["streak"] = task.streak or 0
        if detailed:
            fields["schedule"] = schedule_view(task, clock)
            fields["next_due"] = [clock.local_date(when) for when in task.nextDue[:MAX_NEXT_DUE]]
    elif kind == "todo":
        due = todo_due_date(task, clock)
        if due is not None:
            fields["due_date"] = due
            fields["overdue"] = not task.completed and due < clock.today()
    elif kind == "habit":
        fields["counter_up"] = task.counterUp or 0
        fields["counter_down"] = task.counterDown or 0
        if detailed:
            fields["positive"] = bool(task.up)
            fields["negative"] = bool(task.down)
    else:
        fields["cost"] = task.value or 0

    if detailed:
        fields["alias"] = task.alias
        fields["created_at"] = task.createdAt
        fields["updated_at"] = task.updatedAt
    return TaskView(**fields)


def schedule_view(task: TaskData, clock: UserClock) -> ScheduleView | None:
    if task.frequency is None:
        return None
    view: dict[str, Any] = {"frequency": task.frequency.value, "every_x": task.everyX or 1}
    if task.frequency is HabiticaFrequency.WEEKLY:
        repeat = task.repeat
        view["weekdays"] = [
            day for day, key in REPEAT_KEY_BY_WEEKDAY.items() if getattr(repeat, key, False)
        ]
    elif task.frequency is HabiticaFrequency.MONTHLY:
        view["days_of_month"] = list(task.daysOfMonth)
        view["weeks_of_month"] = list(task.weeksOfMonth)
    if task.startDate is not None:
        view["start_date"] = clock.local_date(task.startDate)
    return ScheduleView(**view)


def to_next_level(level: int | None) -> int | None:
    """XP needed for the next level, as Habitica computes it (common/script/statHelpers.js).

    Habitica only includes ``toNextLevel``, ``maxHealth`` and ``maxMP`` when the whole user
    document is requested, not with ``userFields`` projections.
    """
    if level is None:
        return None
    if level < 5:
        return 25 * level
    if level == 5:
        return 150
    return math.floor(((level**2) * 0.25 + 10 * level + 139.75) / 10 + 0.5) * 10  # JS Math.round


def player_stats(user: UserData) -> PlayerStats:
    stats = user.stats
    return PlayerStats(
        name=user.profile.name,
        level=stats.lvl,
        character_class=stats.Class.value if stats.Class else None,
        hp=_round(stats.hp),
        max_hp=stats.maxHealth or MAX_HEALTH,
        mp=_round(stats.mp),
        max_mp=stats.maxMP,  # depends on gear and class: only shown when Habitica sends it
        exp=_round(stats.exp),
        exp_to_next_level=stats.toNextLevel or to_next_level(stats.lvl),
        gold=_round(stats.gp),
    )


def tag_view(tag: TagsUser, *, task_count: int | None = None) -> TagView:
    return TagView(
        id=str(tag.id),
        name=tag.name or "",
        tasks=task_count,
        challenge=True if tag.challenge else None,
    )


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)
