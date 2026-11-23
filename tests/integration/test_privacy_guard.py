"""Personal or secret data never leaves the server unless the user turned the guard off."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import Client

from habitica_mcp.config import Settings
from habitica_mcp.server import create_server
from tests.fake_habitica import FakeHabitica
from tests.helpers import call, call_error


def writes(fake: FakeHabitica) -> list[str]:
    return [f"{r.method} {r.path}" for r in fake.api_requests() if r.method != "GET"]


async def test_every_write_tool_refuses_sensitive_data_without_calling_habitica(
    client: Client[Any], fake: FakeHabitica
) -> None:
    task = fake.add_task(type="todo", text="Llamar al banco", checklist=["Preguntar horario"])

    errors = [
        await call_error(
            client, "habitica_create_task", task_type="todo", text="Llamar al 300 123 4567"
        ),
        await call_error(
            client,
            "habitica_create_task",
            task_type="todo",
            text="Pagar la tarjeta",
            checklist=["Tarjeta 4111 1111 1111 1111"],
        ),
        await call_error(client, "habitica_update_task", task=task["id"], notes="Clave: perro123"),
        await call_error(
            client,
            "habitica_manage_checklist",
            task=task["id"],
            action="add",
            text="Escribir a ana@example.com",
        ),
        await call_error(client, "habitica_manage_tags", action="create", name="cc 1023456789"),
    ]

    assert writes(fake) == []
    assert all(error.startswith("Not sent to Habitica") for error in errors)
    assert "text: a phone number (***67)" in errors[0]
    assert "4567" not in errors[0]
    assert "checklist: a card number" in errors[1]
    assert "notes: a password or secret" in errors[2]
    assert "an email address" in errors[3]
    assert "an identity document number" in errors[4]
    assert "Rewrite it in general terms" in errors[0]


async def test_clean_text_is_sent_unchanged(client: Client[Any], fake: FakeHabitica) -> None:
    result = await call(
        client,
        "habitica_create_task",
        task_type="todo",
        text="Pagar $1.500.000 de matrícula el 2026-11-28",
        checklist=["Reto 150: 3x15 swings"],
    )

    assert result["task"]["text"] == "Pagar $1.500.000 de matrícula el 2026-11-28"
    assert writes(fake) == ["POST /api/v3/tasks/user"]


async def test_redact_mode_replaces_the_data(
    make_settings: Callable[..., Settings], fake: FakeHabitica
) -> None:
    async with Client(create_server(make_settings(privacy="redact"))) as client:
        result = await call(
            client,
            "habitica_create_task",
            task_type="todo",
            text="Llamar al 300 123 4567",
            notes="Correo ana@example.com",
            checklist=["Preguntar horario"],
        )

    stored = fake.tasks[result["task"]["id"]]
    assert stored["text"] == "Llamar al [redacted]"
    assert stored["notes"] == "Correo [redacted]"
    assert [item["text"] for item in stored["checklist"]] == ["Preguntar horario"]


async def test_off_mode_sends_text_as_is(
    make_settings: Callable[..., Settings], fake: FakeHabitica
) -> None:
    async with Client(create_server(make_settings(privacy="off"))) as client:
        result = await call(
            client, "habitica_create_task", task_type="todo", text="Llamar al 300 123 4567"
        )

    assert fake.tasks[result["task"]["id"]]["text"] == "Llamar al 300 123 4567"


def test_privacy_setting_is_read_from_the_environment(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("HABITICA_MCP_PRIVACY", " Redact ")
    assert Settings().privacy == "redact"
    assert Settings(privacy="off").privacy == "off"
