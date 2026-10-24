"""Create, update, score, checklist, tags and delete against the fake Habitica API."""

from __future__ import annotations

from typing import Any

from fastmcp import Client

from tests.fake_habitica import FakeHabitica
from tests.helpers import call, call_error

CHALLENGE_ID = "99999999-9999-4999-8999-999999999999"

# ---------------------------------------------------------------------------- create


async def test_create_todo_stores_local_midnight_and_creates_missing_tags(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.add_tag("Salud")

    result = await call(
        client,
        "habitica_create_task",
        task_type="todo",
        text="Llamar al dentista",
        difficulty="hard",
        due_date="2026-09-18",
        tags=["salud", "Llamadas"],
        checklist=["Buscar número", "  "],
        notes="Preguntar por limpieza",
    )

    assert result["status"] == "done"
    assert result["created_tags"] == ["Llamadas"]
    task = result["task"]
    assert (task["due_date"], task["difficulty"], task["tags"]) == (
        "2026-09-18",
        "hard",
        ["Salud", "Llamadas"],
    )
    assert [item["text"] for item in task["checklist"]] == ["Buscar número"]

    stored = fake.tasks[task["id"]]
    assert stored["date"] == "2026-09-18T05:00:00+00:00"  # midnight in UTC-5
    assert stored["priority"] == 2
    assert stored["tags"] == [fake.tag_id("Salud"), fake.tag_id("Llamadas")]


async def test_create_daily_habit_and_reward(client: Client[Any], fake: FakeHabitica) -> None:
    daily = await call(
        client,
        "habitica_create_task",
        task_type="daily",
        text="Gimnasio",
        schedule={"frequency": "weekly", "weekdays": ["mon", "wed", "fri"]},
    )
    monthly = await call(
        client,
        "habitica_create_task",
        task_type="daily",
        text="Pagar renta",
        schedule={"frequency": "monthly", "days_of_month": [1, 15]},
    )
    habit = await call(
        client,
        "habitica_create_task",
        task_type="habit",
        text="Fumar",
        habit_positive=False,
        habit_negative=True,
    )
    reward = await call(client, "habitica_create_task", task_type="reward", text="Helado", cost=15)

    stored_daily = fake.tasks[daily["task"]["id"]]
    assert stored_daily["repeat"] == {
        "m": True,
        "t": False,
        "w": True,
        "th": False,
        "f": True,
        "s": False,
        "su": False,
    }
    assert stored_daily["startDate"] == "2026-09-16T05:00:00+00:00"
    assert daily["task"]["due_today"] is True  # 2026-09-16 is a Wednesday
    assert fake.tasks[monthly["task"]["id"]]["daysOfMonth"] == [1, 15]
    assert monthly["task"]["due_today"] is False
    assert (fake.tasks[habit["task"]["id"]]["up"], fake.tasks[habit["task"]["id"]]["down"]) == (
        False,
        True,
    )
    assert "priority" not in fake.requests[-1].body
    assert reward["task"]["cost"] == 15


async def test_create_rejects_fields_that_do_not_fit_the_type(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.add_tag("Casa")
    wrong_type = await call_error(
        client,
        "habitica_create_task",
        task_type="habit",
        text="Leer",
        due_date="2026-09-20",
        checklist=["x"],
    )
    no_new_tags = await call_error(
        client,
        "habitica_create_task",
        task_type="todo",
        text="Leer",
        tags=["Nueva"],
        create_missing_tags=False,
    )
    bad_schedule = await call_error(
        client,
        "habitica_create_task",
        task_type="daily",
        text="Leer",
        schedule={"frequency": "daily", "weekdays": ["mon"]},
    )

    assert wrong_type == (
        "Invalid fields for a habit: due dates only apply to to-dos (dailies use schedule); "
        "checklists only exist on dailies and to-dos."
    )
    assert "Tag 'Nueva' does not exist (existing tags: Casa)" in no_new_tags
    assert bad_schedule == "weekdays only apply when frequency is 'weekly'."
    assert fake.tasks == {}


# ---------------------------------------------------------------------------- update


async def test_update_changes_only_given_fields_and_tags(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.add_tag("Casa")
    fake.add_tag("Urgente")
    task = fake.add_task(type="todo", text="Limpiar", tags=["Casa", "Urgente"], due_in_days=1)

    result = await call(
        client,
        "habitica_update_task",
        task="limpiar",
        text="Limpiar la cocina",
        difficulty="medium",
        add_tags=["Fin de semana"],
        remove_tags=["urgente"],
        clear_due_date=True,
    )

    assert result["status"] == "done"
    assert result["created_tags"] == ["Fin de semana"]
    assert result["task"]["tags"] == ["Casa", "Fin de semana"]
    stored = fake.tasks[task["id"]]
    assert (stored["text"], stored["priority"], "date" in stored) == (
        "Limpiar la cocina",
        1.5,
        False,
    )
    assert set(fake.requests[-1].body) == {"text", "priority", "date", "tags"}


async def test_update_without_changes_does_not_call_habitica(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.add_task(type="todo", text="Leer", priority=1)

    result = await call(client, "habitica_update_task", task="Leer", text="Leer", difficulty="easy")

    assert result["status"] == "no_change"
    assert all(r.method == "GET" for r in fake.api_requests())


async def test_update_protects_challenge_tasks(client: Client[Any], fake: FakeHabitica) -> None:
    fake.add_task(
        type="daily", text="Reto: correr", challenge={"id": CHALLENGE_ID, "shortName": "Run club"}
    )

    message = await call_error(client, "habitica_update_task", task="Reto", text="Otra cosa")
    notes = await call(client, "habitica_update_task", task="Reto", notes="Ok")

    assert message == (
        "'Reto: correr' belongs to the challenge 'Run club': only its notes, tags and "
        "checklist can be changed."
    )
    assert notes["status"] == "done"
    assert notes["task"]["challenge"] == "Run club"


# ----------------------------------------------------------------------------- score


async def test_complete_todo_reports_stats_changes_and_drops(
    client: Client[Any], fake: FakeHabitica
) -> None:
    task = fake.add_task(type="todo", text="Enviar informe", priority=2)
    fake.next_drop = {"dialog": "You found a Wolf Egg!", "key": "Wolf", "type": "eggs"}

    result = await call(client, "habitica_score_task", task="informe")

    assert result["status"] == "done"
    assert result["message"] == "Completed the to-do 'Enviar informe'."
    assert result["task"]["completed"] is True
    assert result["changes"] == {"mp": 1.0, "exp": 20.0, "gold": 4.0}
    assert result["stats"]["gold"] == 29.0
    assert result["drop"] == "You found a Wolf Egg!"
    assert fake.tasks[task["id"]]["completed"] is True
    assert len(fake.api_requests()) == 3  # tasks + user + score


async def test_score_is_a_no_op_when_the_state_already_matches(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.add_task(type="daily", text="Meditar", completed=True)

    again = await call(client, "habitica_score_task", task="Meditar")
    undo = await call(client, "habitica_score_task", task="Meditar", direction="down")

    assert again["status"] == "no_change"
    assert undo["status"] == "done"
    assert undo["task"]["completed"] is False


async def test_undo_a_completed_todo_found_in_completed_list(
    client: Client[Any], fake: FakeHabitica
) -> None:
    task = fake.add_task(
        type="todo", text="Pagar luz", completed=True, dateCompleted="2026-09-15T10:00:00Z"
    )

    result = await call(client, "habitica_score_task", task="Pagar luz", direction="down")

    assert result["status"] == "done"
    assert fake.tasks[task["id"]]["completed"] is False


async def test_daily_scoring_warns_when_the_day_has_not_started(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.user["lastCron"] = "2026-09-15T14:00:00Z"
    fake.add_task(type="daily", text="Meditar")

    result = await call(client, "habitica_score_task", task="Meditar")

    assert any("new day" in warning for warning in result["warnings"])


async def test_habit_directions_must_be_enabled(client: Client[Any], fake: FakeHabitica) -> None:
    fake.add_task(type="habit", text="Beber agua", up=True, down=False)

    plus = await call(client, "habitica_score_task", task="agua")
    minus = await call_error(client, "habitica_score_task", task="agua", direction="down")

    assert plus["task"]["counter_up"] == 1
    assert "has no - button" in minus


async def test_buying_a_reward_needs_confirmation_and_gold(
    client: Client[Any], fake: FakeHabitica
) -> None:
    cheap = fake.add_task(type="reward", text="Café", value=5)
    fake.add_task(type="reward", text="Viaje", value=500)

    preview = await call(client, "habitica_score_task", task="Café")
    bought = await call(client, "habitica_score_task", task=cheap["id"], confirm=True)
    too_expensive = await call_error(client, "habitica_score_task", task="Viaje", confirm=True)

    assert preview["status"] == "confirmation_required"
    assert f"task='{cheap['id']}' and confirm=true" in preview["message"]
    assert bought["status"] == "done"
    assert bought["changes"] == {"gold": -5.0}
    assert fake.user["stats"]["gp"] == 20.0
    assert too_expensive == "Not enough gold: 'Viaje' costs 500 and the user has 20.00."


async def test_habitica_business_errors_are_not_reported_as_bad_credentials(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.add_task(type="todo", text="Leer")
    fake.fail_next(
        401, error="NotAuthorized", message="Your session is outdated. Please refresh or sync."
    )

    # The failure hits the first request (the task list), as Habitica would.
    message = await call_error(client, "habitica_score_task", task="Leer")

    assert (
        message == "Habitica refused the action: Your session is outdated. Please refresh or sync."
    )


# ------------------------------------------------------------------ checklist / tags


async def test_checklist_add_rename_toggle_and_confirmed_remove(
    client: Client[Any], fake: FakeHabitica
) -> None:
    task = fake.add_task(type="todo", text="Mudanza", checklist=["Cajas"])

    added = await call(
        client, "habitica_manage_checklist", task="Mudanza", action="add", text="Cinta"
    )
    renamed = await call(
        client,
        "habitica_manage_checklist",
        task="Mudanza",
        action="rename",
        item="cajas",
        text="Cajas grandes",
    )
    toggled = await call(
        client, "habitica_manage_checklist", task="Mudanza", action="toggle", item="Cinta"
    )
    preview = await call(
        client, "habitica_manage_checklist", task="Mudanza", action="remove", item="Cinta"
    )
    removed = await call(
        client,
        "habitica_manage_checklist",
        task="Mudanza",
        action="remove",
        item="Cinta",
        confirm=True,
    )

    assert added["task"]["checklist_progress"] == "0/2"
    assert renamed["message"] == "Renamed the checklist item 'Cajas' to 'Cajas grandes'."
    assert toggled["message"] == "Checked 'Cinta' in 'Mudanza'."
    assert preview["status"] == "confirmation_required"
    assert removed["status"] == "done"
    assert [item["text"] for item in fake.tasks[task["id"]]["checklist"]] == ["Cajas grandes"]


async def test_checklist_needs_a_daily_or_todo_and_required_arguments(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.add_task(type="habit", text="Agua")
    fake.add_task(type="todo", text="Viaje")

    on_habit = await call_error(
        client, "habitica_manage_checklist", task="Agua", action="add", text="x"
    )
    missing = await call_error(client, "habitica_manage_checklist", task="Viaje", action="add")

    assert on_habit == "Checklists only exist on dailies and to-dos; 'Agua' is a habit."
    assert missing == "'text' is required when action is 'add'."


async def test_manage_tags_create_rename_and_confirmed_delete(
    client: Client[Any], fake: FakeHabitica
) -> None:
    fake.add_tag("Casa")
    task = fake.add_task(type="todo", text="Barrer", tags=["Casa"])

    created = await call(client, "habitica_manage_tags", action="create", name="Trabajo")
    duplicate = await call(client, "habitica_manage_tags", action="create", name="casa")
    renamed = await call(client, "habitica_manage_tags", action="rename", tag="Casa", name="Hogar")
    preview = await call(client, "habitica_manage_tags", action="delete", tag="Hogar")
    deleted = await call(client, "habitica_manage_tags", action="delete", tag="Hogar", confirm=True)
    unknown = await call_error(client, "habitica_manage_tags", action="delete", tag="Nope")

    assert created["status"] == "done"
    assert duplicate["status"] == "no_change"
    assert renamed["tag"]["name"] == "Hogar"
    assert preview["status"] == "confirmation_required"
    assert "removes it from 1 tasks" in preview["message"]
    assert deleted["status"] == "done"
    assert fake.tasks[task["id"]]["tags"] == []
    assert [t["name"] for t in fake.user["tags"]] == ["Trabajo"]
    assert unknown == "Tag 'Nope' does not exist. Existing tags: Trabajo."


# ---------------------------------------------------------------------------- delete


async def test_delete_previews_then_deletes(client: Client[Any], fake: FakeHabitica) -> None:
    task = fake.add_task(type="todo", text="Tarea vieja")

    preview = await call(client, "habitica_delete_task", task="Tarea vieja")
    assert preview["status"] == "confirmation_required"
    assert task["id"] in fake.tasks

    deleted = await call(client, "habitica_delete_task", task=task["id"], confirm=True)
    assert deleted["status"] == "done"
    assert task["id"] not in fake.tasks


async def test_delete_explains_challenge_tasks(client: Client[Any], fake: FakeHabitica) -> None:
    fake.add_task(
        type="daily", text="Reto diario", challenge={"id": CHALLENGE_ID, "shortName": "30 días"}
    )

    message = await call_error(client, "habitica_delete_task", task="Reto diario", confirm=True)

    assert "belongs to the challenge '30 días'" in message
    assert all(r.method == "GET" for r in fake.api_requests())


async def test_a_typical_session_fits_in_habiticas_request_budget(
    client: Client[Any], fake: FakeHabitica
) -> None:
    """The live walkthrough that once hit the limit: writes must not force full refetches."""
    tag = "habitica-mcp-prueba"
    await call(client, "habitica_get_overview")
    todo = await call(
        client,
        "habitica_create_task",
        task_type="todo",
        text="[habitica-mcp] Probar",
        due_date="2026-09-17",
        tags=[tag],
        checklist=["Crear", "Marcar"],
    )
    todo_id = todo["task"]["id"]
    await call(
        client, "habitica_create_task", task_type="daily", text="[habitica-mcp] Diaria", tags=[tag]
    )
    await call(
        client, "habitica_create_task", task_type="habit", text="[habitica-mcp] Hábito", tags=[tag]
    )
    await call(
        client,
        "habitica_create_task",
        task_type="reward",
        text="[habitica-mcp] Premio",
        cost=5,
        tags=[tag],
    )
    listed = await call(client, "habitica_list_tasks", tag=tag)
    await call(client, "habitica_get_task", task="probar")
    await call(client, "habitica_update_task", task=todo_id, difficulty="hard")
    await call(client, "habitica_manage_checklist", task=todo_id, action="toggle", item="crear")
    await call(client, "habitica_score_task", task="hábito")
    await call(client, "habitica_score_task", task=todo_id)
    undone = await call(client, "habitica_score_task", task=todo_id, direction="down")
    preview = await call(client, "habitica_score_task", task="premio")
    await call(client, "habitica_delete_task", task="premio")
    tags = await call(client, "habitica_list_tags")

    assert listed["total_matching"] == 4
    assert undone["status"] == "done"
    assert preview["status"] == "confirmation_required"
    assert [t["tasks"] for t in tags["tags"] if t["name"] == tag] == [4]
    # 16 tool calls: 22 requests now, 28 before writes patched the cache (Habitica allows 30/min).
    assert len(fake.api_requests()) <= 24, [(r.method, r.path) for r in fake.api_requests()]
