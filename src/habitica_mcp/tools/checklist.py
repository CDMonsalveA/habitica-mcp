"""``habitica_manage_checklist``: checklist items of dailies and to-dos."""

from __future__ import annotations

from typing import Annotated, Literal

from fastmcp import Context, FastMCP
from pydantic import Field

from ..errors import ToolInputError
from ..gateway import HabiticaGateway
from ..models import TaskResult
from ..projections import tag_names_by_id, task_type_name, task_view
from ..resolve import find_checklist_item, find_task
from ._common import DESTRUCTIVE, WRITE_TAG, Confirm, TaskReference, gateway_for, required

ChecklistAction = Literal["add", "rename", "toggle", "remove"]


async def manage_checklist(
    gateway: HabiticaGateway,
    *,
    reference: str,
    action: ChecklistAction,
    item: str | None = None,
    text: str | None = None,
    confirm: bool = False,
) -> TaskResult:
    task = await find_task(gateway, reference)
    kind = task_type_name(task)
    if kind not in ("daily", "todo"):
        raise ToolInputError(
            f"Checklists only exist on dailies and to-dos; '{task.text}' is a {kind}."
        )
    clock, tags = await gateway.get_context()
    names = tag_names_by_id(tags)
    task_id = str(task.id)

    if action == "add":
        new_text = required(text, "text", action)
        updated = await gateway.add_checklist_item(task_id, new_text)
        message = f"Added '{new_text}' to the checklist of '{task.text}'."
    else:
        entry = find_checklist_item(task, required(item, "item", action))
        entry_id = str(entry.id)
        if action == "rename":
            new_text = required(text, "text", action)
            updated = await gateway.update_checklist_item(task_id, entry_id, new_text)
            message = f"Renamed the checklist item '{entry.text}' to '{new_text}'."
        elif action == "toggle":
            updated = await gateway.score_checklist_item(task_id, entry_id)
            state = "Unchecked" if entry.completed else "Checked"
            message = f"{state} '{entry.text}' in '{task.text}'."
        else:
            if not confirm:
                return TaskResult(
                    status="confirmation_required",
                    message=f"This removes '{entry.text}' from the checklist of '{task.text}'. "
                    f"Ask the user to confirm, then call again with item='{entry_id}' and "
                    "confirm=true.",
                    task=task_view(task, clock=clock, tag_names=names, detailed=True),
                )
            updated = await gateway.delete_checklist_item(task_id, entry_id)
            message = f"Removed '{entry.text}' from the checklist of '{task.text}'."

    return TaskResult(
        status="done",
        message=message,
        task=task_view(updated, clock=clock, tag_names=names, detailed=True),
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="habitica_manage_checklist",
        title="Habitica: edit a checklist",
        annotations=DESTRUCTIVE,
        tags={WRITE_TAG},
    )
    async def habitica_manage_checklist(
        ctx: Context,
        task: TaskReference,
        action: Annotated[
            ChecklistAction,
            Field(
                description="'add' a new item; 'rename' an item; 'toggle' checks or unchecks an "
                "item; 'remove' deletes an item (needs confirm=true)"
            ),
        ],
        item: Annotated[
            str | None,
            Field(max_length=300, description="Existing item id or text (rename, toggle, remove)"),
        ] = None,
        text: Annotated[
            str | None, Field(max_length=300, description="Item text (add, rename)")
        ] = None,
        confirm: Confirm = False,
    ) -> TaskResult:
        """Add, rename, check/uncheck or remove checklist items of a daily or to-do. Returns the
        task with its checklist item ids."""
        return await manage_checklist(
            await gateway_for(ctx),
            reference=task,
            action=action,
            item=item,
            text=text,
            confirm=confirm,
        )
