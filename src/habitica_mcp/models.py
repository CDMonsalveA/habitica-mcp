"""Tool inputs and outputs.

Outputs omit empty fields when serialized (``exclude_if``) so responses stay small,
while the JSON schema still documents every field.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TaskType = Literal["habit", "daily", "todo", "reward"]
Difficulty = Literal["trivial", "easy", "medium", "hard"]
Frequency = Literal["daily", "weekly", "monthly", "yearly"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ResponseFormat = Literal["concise", "detailed"]
Status = Literal["done", "confirmation_required", "no_change"]


def _is_none(value: object) -> bool:
    return value is None


def _is_empty(value: object) -> bool:
    return value is None or value == []


def omitted(description: str | None = None) -> Any:
    """An optional field that disappears from the output when it is ``None``."""
    return Field(default=None, exclude_if=_is_none, description=description)


def omitted_list(description: str | None = None) -> Any:
    """A list field that disappears from the output when it is empty."""
    return Field(default_factory=list, exclude_if=_is_empty, description=description)


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- task views


class ChecklistItemView(_Output):
    id: str
    text: str
    completed: bool


class ScheduleView(_Output):
    frequency: Frequency
    every_x: int
    weekdays: list[Weekday] = omitted_list("Weekly: days of the week the daily is due")
    days_of_month: list[int] = omitted_list("Monthly: days of the month the daily is due")
    weeks_of_month: list[int] = omitted_list("Monthly: weeks of the month (0 = first)")
    start_date: date | None = omitted()


class TaskView(_Output):
    id: str
    type: TaskType
    text: str
    notes: str | None = omitted()
    difficulty: Difficulty | None = omitted()
    completed: bool | None = omitted("Dailies and to-dos: checked off")
    due_today: bool | None = omitted("Dailies: due in the user's current Habitica day")
    due_date: date | None = omitted("To-dos: due date in the user's calendar")
    overdue: bool | None = omitted("To-dos: past the due date and not completed")
    streak: int | None = omitted()
    counter_up: int | None = omitted("Habits: times scored + in the current period")
    counter_down: int | None = omitted("Habits: times scored - in the current period")
    positive: bool | None = omitted("Habits: the + button is enabled")
    negative: bool | None = omitted("Habits: the - button is enabled")
    cost: float | None = omitted("Rewards: price in gold")
    tags: list[str] = omitted_list("Tag names")
    checklist_progress: str | None = omitted("Checked items / total items")
    checklist: list[ChecklistItemView] = omitted_list()
    schedule: ScheduleView | None = omitted()
    next_due: list[date] = omitted_list("Dailies: next due dates")
    challenge: str | None = omitted("Name of the challenge this task belongs to")
    alias: str | None = omitted()
    created_at: datetime | None = omitted()
    updated_at: datetime | None = omitted()


class PlayerStats(_Output):
    name: str | None = omitted()
    level: int | None = omitted()
    character_class: str | None = omitted()
    hp: float | None = omitted()
    max_hp: int | None = omitted()
    mp: float | None = omitted()
    max_mp: int | None = omitted()
    exp: float | None = omitted()
    exp_to_next_level: int | None = omitted()
    gold: float | None = omitted()


class StatChanges(_Output):
    hp: float | None = omitted()
    mp: float | None = omitted()
    exp: float | None = omitted()
    gold: float | None = omitted()


class TagView(_Output):
    id: str
    name: str
    tasks: int | None = omitted("Active tasks using this tag")
    challenge: bool | None = omitted("Tag created by a challenge")


# ------------------------------------------------------------------ results


class Overview(_Output):
    today: date = Field(description="The user's current Habitica day")
    player: PlayerStats
    needs_cron: bool = Field(description="Habitica has not started the user's new day yet")
    warnings: list[str] = omitted_list()
    dailies_due: list[TaskView] = Field(description="Dailies due today and not completed yet")
    dailies_due_total: int
    dailies_completed: int
    todos_overdue: list[TaskView] = omitted_list()
    todos_due_today: list[TaskView] = omitted_list()
    todos_upcoming: list[TaskView] = omitted_list("Due within the next 7 days")
    todos_without_due_date: int
    habits: list[TaskView] = omitted_list()
    rewards: int
    note: str | None = omitted()


class TaskList(_Output):
    tasks: list[TaskView]
    total_matching: int
    truncated: bool
    note: str | None = omitted()


class TaskResult(_Output):
    status: Status
    message: str
    task: TaskView | None = omitted()
    created_tags: list[str] = omitted_list("Tags that did not exist and were created")


class ScoreResult(_Output):
    status: Status
    message: str
    task: TaskView | None = omitted()
    stats: PlayerStats | None = omitted("Character stats after scoring")
    changes: StatChanges | None = omitted("Difference from before scoring")
    level_up: bool | None = omitted()
    drop: str | None = omitted("Item found while scoring")
    warnings: list[str] = omitted_list()


class TagsResult(_Output):
    status: Status
    message: str
    tags: list[TagView] = omitted_list()
    tag: TagView | None = omitted()


# ------------------------------------------------------------------- inputs


class DailySchedule(BaseModel):
    """When a daily is due."""

    model_config = ConfigDict(extra="forbid")

    frequency: Frequency = Field(
        default="weekly",
        description="'daily' = every X days; 'weekly' = on chosen weekdays every X weeks; "
        "'monthly' = on chosen days of the month every X months; 'yearly' = every X years",
    )
    every_x: int = Field(default=1, ge=1, le=366, description="Repeat interval X")
    weekdays: list[Weekday] | None = Field(
        default=None, description="Weekly only: days it is due. Default: every day"
    )
    days_of_month: list[Annotated[int, Field(ge=1, le=31)]] | None = Field(
        default=None, description="Monthly only: days of the month it is due"
    )
    start_date: date | None = Field(
        default=None, description="First day it can be due (YYYY-MM-DD). Default: today"
    )
