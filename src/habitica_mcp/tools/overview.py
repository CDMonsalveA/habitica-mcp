"""``habitica_get_overview``: the user's day at a glance."""

from __future__ import annotations

from datetime import date, timedelta
from functools import partial

from fastmcp import Context, FastMCP
from habiticalib import TaskData, TaskType

from ..const import (
    LOW_HP_THRESHOLD,
    MAX_HEALTH,
    OVERVIEW_HABITS_CAP,
    OVERVIEW_LIST_CAP,
    UPCOMING_DAYS,
)
from ..cron import needs_cron
from ..gateway import CLOCK_FIELDS, CRON_FIELDS, HabiticaGateway
from ..models import Overview
from ..projections import player_stats, tag_names_by_id, task_view, todo_due_date
from ._common import CRON_WARNING, READ_ONLY, READ_TAG, Format, gateway_for

OVERVIEW_USER_FIELDS = ("profile.name", "stats", "tags", *CLOCK_FIELDS, *CRON_FIELDS)


async def build_overview(gateway: HabiticaGateway, *, detailed: bool = False) -> Overview:
    """Two API calls: the user (stats, tags, time zone, last cron) and the active tasks."""
    user = await gateway.get_user(OVERVIEW_USER_FIELDS)
    tasks = await gateway.get_tasks()
    clock = gateway.clock_for(user)
    today = clock.today()
    view = partial(task_view, clock=clock, tag_names=tag_names_by_id(user.tags), detailed=detailed)

    due_dailies = [t for t in tasks if t.Type is TaskType.DAILY and t.isDue]
    pending_dailies = [t for t in due_dailies if not t.completed]

    overdue: list[tuple[date, TaskData]] = []
    due_today: list[TaskData] = []
    upcoming: list[tuple[date, TaskData]] = []
    without_date = 0
    horizon = today + timedelta(days=UPCOMING_DAYS)
    for todo in (t for t in tasks if t.Type is TaskType.TODO and not t.completed):
        due = todo_due_date(todo, clock)
        if due is None:
            without_date += 1
        elif due < today:
            overdue.append((due, todo))
        elif due == today:
            due_today.append(todo)
        elif due <= horizon:
            upcoming.append((due, todo))
    overdue.sort(key=lambda pair: pair[0])
    upcoming.sort(key=lambda pair: pair[0])
    habits = [t for t in tasks if t.Type is TaskType.HABIT]

    pending_cron = await needs_cron(gateway, user, clock)
    warnings: list[str] = []
    if pending_cron:
        warnings.append(CRON_WARNING)
    hp = user.stats.hp
    if hp is not None and hp < LOW_HP_THRESHOLD:
        warnings.append(
            f"HP is low ({round(hp, 1)}/{user.stats.maxHealth or MAX_HEALTH}). Missed dailies or "
            "negative habits could make the character faint and lose a level, gold and gear."
        )

    shortened = False

    def cap(items: list[TaskData], limit: int = OVERVIEW_LIST_CAP) -> list[TaskData]:
        nonlocal shortened
        if len(items) > limit:
            shortened = True
        return items[:limit]

    return Overview(
        today=today,
        player=player_stats(user),
        needs_cron=pending_cron,
        warnings=warnings,
        dailies_due=[view(t) for t in cap(pending_dailies)],
        dailies_due_total=len(due_dailies),
        dailies_completed=len(due_dailies) - len(pending_dailies),
        todos_overdue=[view(t) for t in cap([t for _, t in overdue])],
        todos_due_today=[view(t) for t in cap(due_today)],
        todos_upcoming=[view(t) for t in cap([t for _, t in upcoming])],
        todos_without_due_date=without_date,
        habits=[view(t) for t in cap(habits, OVERVIEW_HABITS_CAP)],
        rewards=sum(1 for t in tasks if t.Type is TaskType.REWARD),
        note="Some lists were shortened; use habitica_list_tasks to see all tasks."
        if shortened
        else None,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="habitica_get_overview",
        title="Habitica: today's overview",
        annotations=READ_ONLY,
        tags={READ_TAG},
    )
    async def habitica_get_overview(ctx: Context, response_format: Format = "concise") -> Overview:
        """Snapshot of the user's Habitica day: character stats (HP, mana, XP, gold, level),
        dailies still due today, overdue / due-today / upcoming to-dos and habits, plus
        warnings such as a new day that has not started yet. Start here when the user asks
        what is pending or how they are doing."""
        gateway = await gateway_for(ctx)
        return await build_overview(gateway, detailed=response_format == "detailed")
