"""habitica_get_adherence against the fake Habitica API (tasks seeded with history)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastmcp import Client

from tests.fake_habitica import FakeHabitica
from tests.helpers import call, call_error


def ms(iso: str) -> int:
    """Habitica stores history dates as JavaScript timestamps (milliseconds)."""
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1000)


def cron(day: int, **fields: Any) -> dict[str, Any]:
    """A daily history entry written by the cron of 2026-09-<day> (09:00 local, UTC-5)."""
    return {"date": ms(f"2026-09-{day:02d}T14:00:00"), "value": 1.0, **fields}


def seed(fake: FakeHabitica) -> None:
    fake.add_tag("Salud")
    fake.add_task(
        type="daily", text="Meditar", tags=["Salud"], streak=1,
        history=[
            {"date": ms("2026-07-01T14:00:00"), "value": 5.0, "isDue": True, "completed": True},
            cron(10, isDue=True, completed=True), cron(11, isDue=True, completed=True),
            cron(12, isDue=True, completed=False), cron(13, isDue=True, completed=True),
            cron(14, isDue=False, completed=False), cron(15, isDue=True, completed=True),
            cron(16, isDue=True, completed=False),
        ],
    )  # fmt: skip
    # Entries written before Habitica stored isDue/completed: a rising value means done.
    fake.add_task(
        type="daily", text="Leer",
        history=[cron(14, value=1.0), cron(15, value=2.0), cron(16, value=1.5)],
    )  # fmt: skip
    fake.add_task(type="daily", text="Yoga", isDue=False)
    fake.add_task(
        type="habit", text="Beber agua", tags=["Salud"],
        history=[
            {"date": ms("2026-07-01T20:00:00"), "value": 9.0, "scoredUp": 5, "scoredDown": 0},
            {"date": ms("2026-09-15T20:00:00"), "value": 3.0, "scoredUp": 3, "scoredDown": 0},
            {"date": ms("2026-09-16T13:00:00"), "value": 2.0, "scoredUp": 1, "scoredDown": 2},
        ],
    )  # fmt: skip
    fake.add_task(
        type="habit", text="Comida sana",
        history=[
            {"date": ms("2026-09-14T15:00:00"), "value": 1.0},
            {"date": ms("2026-09-14T18:00:00"), "value": 0.5},
        ],
    )  # fmt: skip
    fake.add_task(type="habit", text="Ejercicio")
    fake.add_task(type="todo", text="Pagar luz")


async def test_adherence_reports_dailies_weakest_first_and_habits_by_use(
    client: Client[Any], fake: FakeHabitica
) -> None:
    seed(fake)

    report = await call(client, "habitica_get_adherence")

    assert (report["period_start"], report["period_end"]) == ("2026-08-17", "2026-09-16")
    assert report["days_recorded"] == 7  # 2026-09-09 .. 2026-09-15
    leer, meditar, yoga = report["dailies"]
    assert leer == {
        "id": leer["id"], "text": "Leer", "due_days": 3, "completed_days": 1, "rate": 0.33,
        "streak": 0, "recent": "-x-", "completed_today": False,
    }  # fmt: skip
    assert meditar | {"id": ""} == {
        "id": "", "text": "Meditar", "due_days": 6, "completed_days": 4, "rate": 0.67,
        "streak": 1, "recent": "xx-xx-", "completed_today": False,
    }  # fmt: skip
    assert yoga | {"id": ""} == {
        "id": "", "text": "Yoga", "due_days": 0, "completed_days": 0, "streak": 0
    }  # fmt: skip
    assert [(h["text"], h["up"], h["down"], h["active_days"]) for h in report["habits"]] == [
        ("Beber agua", 4, 2, 2),
        ("Comida sana", 1, 1, 1),
        ("Ejercicio", 0, 0, 0),
    ]
    assert report["habits"][0]["last_scored"] == "2026-09-16"
    assert "last_scored" not in report["habits"][2]
    assert any("recorded only 7 of the last 30 days" in note for note in report["notes"])


async def test_adherence_filters_by_type_tag_and_period(
    client: Client[Any], fake: FakeHabitica
) -> None:
    seed(fake)

    habits_only = await call(client, "habitica_get_adherence", task_type="habit")
    tagged = await call(client, "habitica_get_adherence", tag="salud")
    week = await call(client, "habitica_get_adherence", days=7, task_type="daily")

    assert "dailies" not in habits_only
    assert habits_only["days_recorded"] == 0
    assert [d["text"] for d in tagged["dailies"]] == ["Meditar"]
    assert [h["text"] for h in tagged["habits"]] == ["Beber agua"]
    assert week["period_start"] == "2026-09-09"
    assert "notes" not in week  # 7 of 7 days recorded
    error = await call_error(client, "habitica_get_adherence", tag="Trabajo")
    assert "does not exist" in error
    assert "Salud" in error


async def test_adherence_costs_two_requests_and_refreshes_after_scoring(
    client: Client[Any], fake: FakeHabitica
) -> None:
    seed(fake)

    await call(client, "habitica_get_adherence")
    await call(client, "habitica_get_adherence", task_type="habit")
    first = len(fake.api_requests())
    await call(client, "habitica_score_task", task="Ejercicio", direction="up")
    report = await call(client, "habitica_get_adherence", task_type="habit")

    assert first == 2  # the second report is served from the cache
    history_reads = [
        r
        for r in fake.api_requests()
        if r.path.endswith("/tasks/user") and r.query.get("history") != "false"
    ]
    assert len(history_reads) == 2  # scoring invalidated the cached history
    assert ("Ejercicio", 1) in [(h["text"], h["up"]) for h in report["habits"]]


async def test_no_habit_scored_note(client: Client[Any], fake: FakeHabitica) -> None:
    fake.add_task(type="habit", text="Ejercicio")

    report = await call(client, "habitica_get_adherence")

    assert report["notes"] == ["No habit was scored in this period."]
    assert report["days_recorded"] == 0
