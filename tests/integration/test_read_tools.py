"""Overview, list, get and list-tags against the fake Habitica API."""

from __future__ import annotations

from typing import Any

from fastmcp import Client

from habitica_mcp.const import MAINTAINER_USER_ID
from tests.fake_habitica import FakeHabitica
from tests.helpers import call, call_error


def seed_day(fake: FakeHabitica) -> dict[str, str]:
    fake.user["stats"].update(hp=12.0, gp=40.0)
    fake.user["lastCron"] = "2026-09-15T14:00:00Z"  # yesterday: the new day has not started
    fake.add_tag("Trabajo")
    return {
        "meditar": fake.add_task(type="daily", text="Meditación", tags=["Trabajo"], streak=4)["id"],
        "done_daily": fake.add_task(type="daily", text="Tender la cama", completed=True)["id"],
        "not_due": fake.add_task(type="daily", text="Yoga", isDue=False)["id"],
        "overdue": fake.add_task(type="todo", text="Pagar luz", due_in_days=-2, priority=2)["id"],
        "today": fake.add_task(type="todo", text="Llamar al dentista", due_in_days=0)["id"],
        "soon": fake.add_task(
            type="todo", text="Renovar pasaporte", due_in_days=3, notes="Llevar fotos"
        )["id"],
        "later": fake.add_task(type="todo", text="Planear viaje", due_in_days=30)["id"],
        "undated": fake.add_task(type="todo", text="Leer libro", tags=["Trabajo"])["id"],
        "old_done": fake.add_task(
            type="todo", text="Enviar informe", completed=True, dateCompleted="2026-09-10T10:00:00Z"
        )["id"],
        "habit": fake.add_task(type="habit", text="Beber agua", counterUp=3)["id"],
        "reward": fake.add_task(type="reward", text="Ver una serie", value=20)["id"],
    }


async def test_overview_summarizes_the_day_in_two_api_calls(
    client: Client[Any], fake: FakeHabitica
) -> None:
    seed_day(fake)

    overview = await call(client, "habitica_get_overview")

    assert overview["today"] == "2026-09-16"
    assert overview["player"] == {
        "name": "Tester", "level": 5, "character_class": "warrior", "hp": 12.0, "max_hp": 50,
        "mp": 30.0, "exp": 10.0, "exp_to_next_level": 150, "gold": 40.0,
    }  # fmt: skip
    assert overview["needs_cron"] is True
    assert any("new day" in warning for warning in overview["warnings"])
    assert any("HP is low" in warning for warning in overview["warnings"])
    assert [t["text"] for t in overview["dailies_due"]] == ["Meditación"]
    assert overview["dailies_due"][0]["tags"] == ["Trabajo"]
    assert (overview["dailies_due_total"], overview["dailies_completed"]) == (2, 1)
    assert [t["text"] for t in overview["todos_overdue"]] == ["Pagar luz"]
    assert [t["text"] for t in overview["todos_due_today"]] == ["Llamar al dentista"]
    assert [t["text"] for t in overview["todos_upcoming"]] == ["Renovar pasaporte"]
    assert overview["todos_without_due_date"] == 1
    assert [t["text"] for t in overview["habits"]] == ["Beber agua"]
    assert overview["rewards"] == 1

    requests = fake.api_requests()
    assert [r.path for r in requests] == ["/api/v3/user", "/api/v3/tasks/user"]
    assert requests[1].query == {"history": "false"}
    assert "stats" in requests[0].query["userFields"]
    assert all(r.headers["x-client"] == f"{MAINTAINER_USER_ID}-habitica-mcp" for r in requests)
    assert all(r.headers["user-agent"].startswith("habitica-mcp/") for r in requests)


async def test_list_filters_and_reuses_cached_reads(
    client: Client[Any], fake: FakeHabitica
) -> None:
    seed_day(fake)

    pending = await call(client, "habitica_list_tasks", status="pending")
    overdue = await call(client, "habitica_list_tasks", status="overdue")
    due_today = await call(client, "habitica_list_tasks", status="due_today", task_type="todo")
    by_tag = await call(client, "habitica_list_tasks", tag="trabajo")
    by_text = await call(client, "habitica_list_tasks", search="PASAPORTE")
    by_notes = await call(client, "habitica_list_tasks", search="fotos", response_format="detailed")

    assert {t["text"] for t in pending["tasks"]} == {
        "Meditación",
        "Pagar luz",
        "Llamar al dentista",
        "Renovar pasaporte",
        "Planear viaje",
        "Leer libro",
    }
    assert [t["text"] for t in overdue["tasks"]] == ["Pagar luz"]
    assert [t["text"] for t in due_today["tasks"]] == ["Llamar al dentista"]
    assert {t["text"] for t in by_tag["tasks"]} == {"Meditación", "Leer libro"}
    assert [t["text"] for t in by_text["tasks"]] == ["Renovar pasaporte"]
    assert by_notes["tasks"][0]["notes"] == "Llevar fotos"
    # One task list + one user (tags and time zone) for all six calls.
    assert len(fake.api_requests()) == 2


async def test_list_completed_includes_recently_completed_todos(
    client: Client[Any], fake: FakeHabitica
) -> None:
    seed_day(fake)

    completed = await call(client, "habitica_list_tasks", status="completed")

    assert {t["text"] for t in completed["tasks"]} == {"Tender la cama", "Enviar informe"}
    assert {"type": "completedTodos", "history": "false"} in [r.query for r in fake.api_requests()]


async def test_list_truncates_with_guidance(client: Client[Any], fake: FakeHabitica) -> None:
    for number in range(5):
        fake.add_task(type="todo", text=f"Tarea {number}")

    page = await call(client, "habitica_list_tasks", limit=2)
    empty = await call(client, "habitica_list_tasks", search="no existe")

    assert page["total_matching"] == 5
    assert page["truncated"] is True
    assert len(page["tasks"]) == 2
    assert "Narrow" in page["note"]
    assert empty == {
        "tasks": [],
        "total_matching": 0,
        "truncated": False,
        "note": "No tasks match these filters.",
    }


async def test_unknown_tag_filter_lists_existing_tags(
    client: Client[Any], fake: FakeHabitica
) -> None:
    seed_day(fake)
    message = await call_error(client, "habitica_list_tasks", tag="Casa")
    assert message == "Tag 'Casa' does not exist. Existing tags: Trabajo."


async def test_get_task_by_text_id_and_completed_todo(
    client: Client[Any], fake: FakeHabitica
) -> None:
    ids = seed_day(fake)

    by_text = await call(client, "habitica_get_task", task="meditacion")
    by_id = await call(client, "habitica_get_task", task=ids["soon"])
    completed = await call(client, "habitica_get_task", task="Enviar informe")

    assert by_text["id"] == ids["meditar"]
    assert by_text["schedule"]["weekdays"] == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    assert by_id["due_date"] == "2026-09-19"
    assert completed["completed"] is True


async def test_get_task_explains_ambiguous_and_missing_references(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.add_task(type="todo", text="Correr 5k")
    fake.add_task(type="todo", text="Correr 10k")

    ambiguous = await call_error(client, "habitica_get_task", task="correr")
    missing = await call_error(client, "habitica_get_task", task="Corer 5k")

    assert "matches several tasks" in ambiguous
    assert "Correr 5k" in ambiguous
    assert "Correr 10k" in ambiguous
    assert missing.startswith("No task matches 'Corer 5k'. Did you mean: 'Correr 5k'")


async def test_list_tags_counts_tasks(client: Client[Any], fake: FakeHabitica) -> None:
    seed_day(fake)
    fake.add_tag("Casa")

    result = await call(client, "habitica_list_tags")

    assert result["message"] == "The user has 2 tags."
    assert [(t["name"], t["tasks"]) for t in result["tags"]] == [("Trabajo", 2), ("Casa", 0)]


async def test_overview_asks_habitica_when_the_time_zone_changed(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.user["lastCron"] = "2026-09-15T14:00:00Z"
    fake.user["preferences"]["timezoneOffsetAtLastCron"] = 240  # travelled since the last cron

    overview = await call(client, "habitica_get_overview")

    assert overview["needs_cron"] is True
    user_requests = [r for r in fake.api_requests() if r.path == "/api/v3/user"]
    assert [("userFields" in r.query) for r in user_requests] == [True, False]
