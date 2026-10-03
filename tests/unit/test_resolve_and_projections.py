from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from habiticalib import TagsUser, TaskData

from habitica_mcp.dates import UserClock
from habitica_mcp.errors import AmbiguousReferenceError, ToolInputError
from habitica_mcp.projections import schedule_view, task_view, to_next_level, truncate
from habitica_mcp.resolve import find_checklist_item, find_tag, match_one, normalize, suggest

CLOCK = UserClock(datetime(2026, 9, 16, 14, 0, tzinfo=UTC), timezone_offset_minutes=300)


def make_task(**fields: Any) -> TaskData:
    base: dict[str, Any] = {
        "id": "11111111-1111-4111-8111-111111111111",
        "type": "todo",
        "text": "Task",
    }
    base.update(fields)
    return TaskData.from_dict(base)


def match_text(items: list[str], reference: str) -> str | None:
    return match_one(
        items, reference, ids=lambda _: [], text=lambda item: item, describe=str, kind="thing"
    )


def test_normalize_ignores_case_accents_and_spacing() -> None:
    assert normalize("  Meditación   DIARIA ") == "meditacion diaria"


def test_match_prefers_exact_text_over_partial_matches() -> None:
    items = ["Leer", "Leer 20 páginas"]
    assert match_text(items, "leer") == "Leer"
    assert match_text(items, "paginas") == "Leer 20 páginas"
    assert match_text(items, "nada") is None


def test_ambiguous_partial_match_lists_candidates() -> None:
    with pytest.raises(
        AmbiguousReferenceError, match="matches several things: Correr 5k; Correr 10k"
    ):
        match_text(["Correr 5k", "Correr 10k"], "correr")


def test_suggestions_use_close_matches() -> None:
    assert suggest("meditr", ["Meditar", "Leer"]) == ["'Meditar'"]


def test_find_tag_is_exact_and_checklist_items_resolve_by_text() -> None:
    tags = [TagsUser(id="22222222-2222-4222-8222-222222222222", name="Trabajo")]  # type: ignore[arg-type]
    assert find_tag(tags, "trabajo") is tags[0]
    assert find_tag(tags, "Trab") is None

    task = make_task(
        checklist=[
            {
                "id": "33333333-3333-4333-8333-333333333333",
                "text": "Comprar pan",
                "completed": False,
            }
        ]
    )
    assert find_checklist_item(task, "pan").text == "Comprar pan"
    with pytest.raises(ToolInputError, match="Items: 'Comprar pan'"):
        find_checklist_item(task, "leche")


def test_concise_todo_view_has_local_due_date_and_overdue_flag() -> None:
    task = make_task(
        notes="n" * 500,
        priority=2,
        date="2026-09-15T05:00:00.000Z",  # local midnight of the 15th in UTC-5
        tags=["22222222-2222-4222-8222-222222222222"],
        checklist=[
            {"id": "33333333-3333-4333-8333-333333333333", "text": "a", "completed": True},
            {"id": "44444444-4444-4444-8444-444444444444", "text": "b", "completed": False},
        ],
    )

    view = task_view(
        task, clock=CLOCK, tag_names={"22222222-2222-4222-8222-222222222222": "Trabajo"}
    )
    dumped = view.model_dump(mode="json", exclude_none=True)

    assert dumped["due_date"] == "2026-09-15"
    assert dumped["overdue"] is True
    assert dumped["difficulty"] == "hard"
    assert dumped["tags"] == ["Trabajo"]
    assert dumped["checklist_progress"] == "1/2"
    assert len(dumped["notes"]) == 160
    assert "checklist" not in dumped


def test_detailed_daily_view_includes_schedule() -> None:
    task = make_task(
        type="daily",
        frequency="weekly",
        everyX=1,
        repeat={"m": True, "t": False, "w": True, "th": False, "f": True, "s": False, "su": False},
        startDate="2026-09-01T05:00:00.000Z",
        isDue=True,
        streak=3,
    )
    view = task_view(task, clock=CLOCK, tag_names={}, detailed=True)

    assert view.due_today is True
    assert view.streak == 3
    assert view.schedule is not None
    assert view.schedule.weekdays == ["mon", "wed", "fri"]
    assert view.schedule.start_date == date(2026, 9, 1)
    assert schedule_view(make_task(type="habit"), CLOCK) is None


def test_views_of_habits_and_rewards() -> None:
    habit = task_view(
        make_task(type="habit", counterUp=2, counterDown=1, up=True, down=False),
        clock=CLOCK,
        tag_names={},
        detailed=True,
    )
    reward = task_view(make_task(type="reward", value=15, priority=1), clock=CLOCK, tag_names={})

    assert (habit.counter_up, habit.counter_down, habit.positive, habit.negative) == (
        2,
        1,
        True,
        False,
    )
    assert reward.cost == 15
    assert reward.difficulty is None


def test_truncate_keeps_short_text() -> None:
    assert truncate(" hola ", 10) == "hola"
    assert truncate("abcdefghij", 5) == "abcd…"


def test_to_next_level_matches_habitica() -> None:
    # Values from Habitica's statHelpers.toNextLevel; 380 was checked against a real level-17 user.
    assert [to_next_level(level) for level in (None, 1, 4, 5, 6, 17, 50)] == [
        None, 25, 100, 150, 210, 380, 1260,
    ]  # fmt: skip
