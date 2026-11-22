"""``habitica_get_adherence``: how consistently the user really does their dailies and habits."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Annotated, Literal

from fastmcp import Context, FastMCP
from habiticalib import EntryHistory, TaskData, TaskType
from pydantic import Field

from ..const import ADHERENCE_LIST_CAP, ADHERENCE_RECENT_DAYS
from ..dates import UserClock
from ..errors import ToolInputError
from ..gateway import HabiticaGateway
from ..models import AdherenceReport, DailyAdherence, HabitAdherence
from ..resolve import find_tag
from ._common import READ_ONLY, READ_TAG, gateway_for, unknown_tag_message


def _dated(history: Sequence[EntryHistory]) -> list[EntryHistory]:
    return sorted((h for h in history if h.date is not None), key=lambda h: h.date)  # type: ignore[arg-type,return-value]


def daily_adherence(
    task: TaskData, clock: UserClock, start: date, today: date
) -> tuple[DailyAdherence, set[date]]:
    """Adherence of one daily, plus the days Habitica recorded for it.

    Habitica appends a history entry when the day ends (at cron), stamped with the cron time:
    the entry describes the previous day. Old entries lack ``isDue``/``completed``; then a
    rising value means the daily was done.
    """
    recorded: set[date] = set()
    outcomes: list[bool] = []
    previous: float | None = None
    for entry in _dated(task.history):
        day = clock.habitica_day(entry.date) - timedelta(days=1)  # type: ignore[arg-type]
        done = entry.completed
        if done is None:
            done = previous is not None and entry.value > previous
        previous = entry.value
        if not start <= day < today:
            continue
        recorded.add(day)
        if entry.isDue is not False:
            outcomes.append(done)
    completed = sum(outcomes)
    recent = outcomes[-ADHERENCE_RECENT_DAYS:]
    return (
        DailyAdherence(
            id=str(task.id),
            text=task.text or "",
            due_days=len(outcomes),
            completed_days=completed,
            rate=round(completed / len(outcomes), 2) if outcomes else None,
            streak=task.streak or 0,
            recent="".join("x" if done else "-" for done in recent) or None,
            completed_today=bool(task.completed) if task.isDue else None,
        ),
        recorded,
    )


def habit_adherence(task: TaskData, clock: UserClock, start: date) -> HabitAdherence:
    """Habitica keeps one history entry per day with ``scoredUp``/``scoredDown`` counts.

    Older entries are one per score without counts; the value change gives the direction.
    """
    up = down = 0
    days: set[date] = set()
    previous = 0.0
    for entry in _dated(task.history):
        day = clock.habitica_day(entry.date)  # type: ignore[arg-type]
        if entry.scoredUp is None and entry.scoredDown is None:
            ups, downs = (1, 0) if entry.value >= previous else (0, 1)
        else:
            ups, downs = entry.scoredUp or 0, entry.scoredDown or 0
        previous = entry.value
        if day < start or not ups + downs:
            continue
        up += ups
        down += downs
        days.add(day)
    return HabitAdherence(
        id=str(task.id),
        text=task.text or "",
        up=up,
        down=down,
        active_days=len(days),
        last_scored=max(days) if days else None,
    )


async def build_adherence(
    gateway: HabiticaGateway,
    *,
    days: int = 30,
    task_type: Literal["daily", "habit"] | None = None,
    tag: str | None = None,
) -> AdherenceReport:
    """Two API calls at most: the tasks with history, and the user's clock and tags."""
    tasks = await gateway.get_tasks_with_history()
    clock, tags = await gateway.get_context()
    if tag:
        found = find_tag(tags, tag)
        if found is None:
            raise ToolInputError(unknown_tag_message(tag, tags))
        tasks = [t for t in tasks if found.id in t.tags]
    today = clock.today()
    start = today - timedelta(days=days)

    dailies: list[DailyAdherence] = []
    recorded: set[date] = set()
    if task_type in (None, "daily"):
        for task in (t for t in tasks if t.Type is TaskType.DAILY):
            report, seen = daily_adherence(task, clock, start, today)
            dailies.append(report)
            recorded |= seen
    habits: list[HabitAdherence] = []
    if task_type in (None, "habit"):
        habits = [habit_adherence(t, clock, start) for t in tasks if t.Type is TaskType.HABIT]

    dailies.sort(key=lambda d: (d.rate is None, d.rate or 0.0, -d.due_days))
    habits.sort(key=lambda h: (-(h.up + h.down), h.text))
    notes: list[str] = []
    if dailies and len(recorded) < days / 2:
        notes.append(
            f"Habitica recorded only {len(recorded)} of the last {days} days. It records a day "
            "when the user opens Habitica the following day, so the other days are unknown. "
            "Opening Habitica every day makes these numbers reliable."
        )
    if habits and not any(h.up + h.down for h in habits):
        notes.append("No habit was scored in this period.")
    if len(dailies) > ADHERENCE_LIST_CAP or len(habits) > ADHERENCE_LIST_CAP:
        notes.append(f"Lists were cut to {ADHERENCE_LIST_CAP}; filter by task_type or tag.")
    return AdherenceReport(
        period_start=start,
        period_end=today,
        days_recorded=len(recorded),
        dailies=dailies[:ADHERENCE_LIST_CAP],
        habits=habits[:ADHERENCE_LIST_CAP],
        notes=notes,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="habitica_get_adherence",
        title="Habitica: adherence report",
        annotations=READ_ONLY,
        tags={READ_TAG},
    )
    async def habitica_get_adherence(
        ctx: Context,
        days: Annotated[int, Field(ge=7, le=90, description="Period length in days")] = 30,
        task_type: Annotated[
            Literal["daily", "habit"] | None, Field(description="Only dailies or only habits")
        ] = None,
        tag: Annotated[str | None, Field(max_length=100, description="Tag name or id")] = None,
    ) -> AdherenceReport:
        """How consistently the user really did their dailies (completion rate over due days,
        streak, recent pattern) and habits (+/- counts, active days) in the last N days.
        Use it for weekly reviews or to decide which tasks to simplify, drop or reinforce."""
        gateway = await gateway_for(ctx)
        return await build_adherence(gateway, days=days, task_type=task_type, tag=tag)
