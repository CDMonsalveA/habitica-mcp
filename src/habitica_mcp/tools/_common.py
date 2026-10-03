"""Pieces shared by the tool modules."""

from __future__ import annotations

from typing import Annotated, cast

from fastmcp import Context
from mcp_types import ToolAnnotations
from pydantic import Field

from ..errors import ToolInputError
from ..gateway import HabiticaGateway
from ..models import ResponseFormat
from ..state import AppState

READ_TAG = "read"
WRITE_TAG = "write"

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
ADDITIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)
IDEMPOTENT_UPDATE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True
)

CRON_WARNING = (
    "Habitica has not started the user's new day yet. Checking off dailies now counts for "
    "the previous day; suggest opening the Habitica app or website first to start the day."
)

TaskReference = Annotated[
    str,
    Field(
        min_length=1,
        max_length=300,
        description="Task id (preferred), alias, or its text. Partial text works when it "
        "matches exactly one task.",
    ),
]
Format = Annotated[
    ResponseFormat,
    Field(
        description="'concise' keeps responses short; 'detailed' adds full notes, checklist "
        "items with ids, schedules and dates."
    ),
]
Confirm = Annotated[
    bool,
    Field(
        description="Set to true only after the user explicitly agreed to this irreversible "
        "action. When false the tool only returns a preview and changes nothing."
    ),
]


async def gateway_for(ctx: Context) -> HabiticaGateway:
    state = cast(AppState, ctx.lifespan_context["state"])
    return await state.gateway()


def required(value: str | None, name: str, action: str) -> str:
    """Fail with guidance when an action-specific argument is missing or blank."""
    if value is None or not value.strip():
        raise ToolInputError(f"'{name}' is required when action is '{action}'.")
    return value.strip()
