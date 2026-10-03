"""Tag tools: ``habitica_list_tags`` and ``habitica_manage_tags``."""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Literal

from fastmcp import Context, FastMCP
from pydantic import Field

from ..errors import ToolInputError
from ..gateway import HabiticaGateway
from ..models import TagsResult
from ..projections import tag_view
from ..resolve import find_tag
from ._common import DESTRUCTIVE, READ_ONLY, READ_TAG, WRITE_TAG, Confirm, gateway_for, required

TagAction = Literal["create", "rename", "delete"]


async def list_tags(gateway: HabiticaGateway) -> TagsResult:
    _, tags = await gateway.get_context()
    tasks = await gateway.get_tasks()
    usage = Counter(str(tag_id) for task in tasks for tag_id in task.tags)
    views = [tag_view(tag, task_count=usage.get(str(tag.id), 0)) for tag in tags]
    return TagsResult(
        status="done",
        message=f"The user has {len(views)} tags." if views else "The user has no tags yet.",
        tags=views,
    )


async def manage_tags(
    gateway: HabiticaGateway,
    *,
    action: TagAction,
    tag: str | None = None,
    name: str | None = None,
    confirm: bool = False,
) -> TagsResult:
    _, tags = await gateway.get_context()

    if action == "create":
        new_name = required(name, "name", action)
        if (existing := find_tag(tags, new_name)) is not None:
            return TagsResult(
                status="no_change",
                message=f"The tag '{existing.name}' already exists.",
                tag=tag_view(existing),
            )
        created = await gateway.create_tag(new_name)
        return TagsResult(
            status="done", message=f"Created the tag '{new_name}'.", tag=tag_view(created)
        )

    reference = required(tag, "tag", action)
    target = find_tag(tags, reference)
    if target is None:
        available = ", ".join(sorted(t.name or "" for t in tags)) or "none"
        raise ToolInputError(f"Tag {reference!r} does not exist. Existing tags: {available}.")

    if action == "rename":
        new_name = required(name, "name", action)
        clash = find_tag(tags, new_name)
        if clash is not None and clash.id != target.id:
            raise ToolInputError(f"A tag named '{clash.name}' already exists.")
        renamed = await gateway.update_tag(str(target.id), new_name)
        return TagsResult(
            status="done",
            message=f"Renamed the tag '{target.name}' to '{new_name}'.",
            tag=tag_view(renamed),
        )

    tasks = await gateway.get_tasks()
    count = sum(1 for task in tasks if target.id in task.tags)
    if not confirm:
        return TagsResult(
            status="confirmation_required",
            message=f"This deletes the tag '{target.name}' and removes it from {count} tasks "
            f"(the tasks are kept). Ask the user to confirm, then call again with "
            f"tag='{target.id}' and confirm=true.",
            tag=tag_view(target, task_count=count),
        )
    await gateway.delete_tag(str(target.id))
    return TagsResult(
        status="done",
        message=f"Deleted the tag '{target.name}' and removed it from {count} tasks.",
        tag=tag_view(target, task_count=count),
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="habitica_list_tags",
        title="Habitica: list tags",
        annotations=READ_ONLY,
        tags={READ_TAG},
    )
    async def habitica_list_tags(ctx: Context) -> TagsResult:
        """List the user's tags with how many active tasks use each one."""
        return await list_tags(await gateway_for(ctx))

    @mcp.tool(
        name="habitica_manage_tags",
        title="Habitica: create, rename or delete tags",
        annotations=DESTRUCTIVE,
        tags={WRITE_TAG},
    )
    async def habitica_manage_tags(
        ctx: Context,
        action: Annotated[
            TagAction,
            Field(description="'delete' removes the tag from every task and needs confirm=true"),
        ],
        tag: Annotated[
            str | None,
            Field(max_length=100, description="Existing tag name or id (rename, delete)"),
        ] = None,
        name: Annotated[
            str | None, Field(max_length=100, description="New tag name (create, rename)")
        ] = None,
        confirm: Confirm = False,
    ) -> TagsResult:
        """Create, rename or delete a tag. To tag tasks use habitica_update_task(add_tags=...)."""
        return await manage_tags(
            await gateway_for(ctx), action=action, tag=tag, name=name, confirm=confirm
        )
