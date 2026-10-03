"""Find tasks, tags and checklist items from what a model or user wrote.

References may be ids, aliases or (partial) text. Matching ignores case and accents so
"meditacion" finds "Meditación". A reference must identify exactly one item; otherwise
the error lists candidates so the model can ask the user or retry with an id.
"""

from __future__ import annotations

import difflib
import unicodedata
from collections.abc import Callable, Sequence
from uuid import UUID

from habiticalib import Checklist, TagsUser, TaskData, TaskFilter

from .const import MAX_SUGGESTIONS
from .errors import AmbiguousReferenceError, TaskNotFoundError, ToolInputError
from .gateway import HabiticaGateway
from .projections import task_type_name


def normalize(text: str) -> str:
    """Case-, accent- and whitespace-insensitive form of a text."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


def is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def match_one[T](
    items: Sequence[T],
    reference: str,
    *,
    ids: Callable[[T], Sequence[str]],
    text: Callable[[T], str],
    describe: Callable[[T], str],
    kind: str,
    allow_partial: bool = True,
) -> T | None:
    """Return the single item matching ``reference`` or ``None`` when nothing matches.

    Order: exact id/alias, exact text, then unique partial text.
    """
    wanted = reference.strip()
    folded = wanted.casefold()
    for item in items:
        if any(candidate and candidate.casefold() == folded for candidate in ids(item)):
            return item

    key = normalize(wanted)
    exact = [item for item in items if normalize(text(item)) == key]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise AmbiguousReferenceError(
            kind, reference, [describe(i) for i in exact[:MAX_SUGGESTIONS]]
        )
    if not allow_partial or not key:
        return None
    partial = [item for item in items if key in normalize(text(item))]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise AmbiguousReferenceError(
            kind, reference, [describe(i) for i in partial[:MAX_SUGGESTIONS]]
        )
    return None


def describe_task(task: TaskData) -> str:
    return f"'{task.text}' ({task_type_name(task)}, id {task.id})"


def _task_ids(task: TaskData) -> list[str]:
    return [str(task.id) if task.id else "", task.alias or ""]


def _task_text(task: TaskData) -> str:
    return task.text or ""


def suggest(reference: str, texts: Sequence[str]) -> list[str]:
    by_key = {normalize(t): t for t in texts if t}
    close = difflib.get_close_matches(normalize(reference), list(by_key), n=3, cutoff=0.6)
    return [f"'{by_key[key]}'" for key in close]


async def find_task(
    gateway: HabiticaGateway,
    reference: str,
    *,
    include_completed_todos: bool = False,
) -> TaskData:
    """Resolve a task reference, reading the task list at most once (plus completed to-dos)."""
    reference = reference.strip()
    if not reference:
        raise ToolInputError("Give the task id, alias or text.")
    tasks = await gateway.get_tasks()
    found = match_one(
        tasks, reference, ids=_task_ids, text=_task_text, describe=describe_task, kind="task"
    )
    if found is not None:
        return found
    pool = list(tasks)
    if include_completed_todos:
        completed = await gateway.get_tasks(TaskFilter.COMPLETED_TODOS)
        found = match_one(
            completed,
            reference,
            ids=_task_ids,
            text=_task_text,
            describe=describe_task,
            kind="task",
        )
        if found is not None:
            return found
        pool.extend(completed)
    if is_uuid(reference):
        # Not in the lists we read (e.g. an older completed to-do): ask for it directly.
        return await gateway.get_task(reference)
    raise TaskNotFoundError(reference, suggest(reference, [t.text or "" for t in pool]))


def find_tag(tags: Sequence[TagsUser], reference: str) -> TagsUser | None:
    """Exact tag match by id or name (no partial matches: tags are short and similar)."""
    return match_one(
        tags,
        reference,
        ids=lambda tag: [str(tag.id) if tag.id else ""],
        text=lambda tag: tag.name or "",
        describe=lambda tag: f"'{tag.name}' (id {tag.id})",
        kind="tag",
        allow_partial=False,
    )


async def resolve_tag_ids(
    gateway: HabiticaGateway,
    tags: Sequence[TagsUser],
    references: Sequence[str],
    *,
    create_missing: bool,
) -> tuple[list[str], list[TagsUser]]:
    """Map tag references to ids, creating missing tags when allowed.

    Returns ``(tag_ids, created_tags)``.
    """
    ids: list[str] = []
    created: list[TagsUser] = []
    known = list(tags)
    for reference in references:
        name = reference.strip()
        if not name:
            continue
        tag = find_tag(known, name)
        if tag is None:
            if not create_missing:
                available = ", ".join(sorted(t.name or "" for t in known)) or "none"
                raise ToolInputError(
                    f"Tag {name!r} does not exist (existing tags: {available}). Use an existing "
                    "tag or allow creating it."
                )
            if is_uuid(name):
                raise ToolInputError(f"No tag has the id {name!r}.")
            tag = await gateway.create_tag(name)
            known.append(tag)
            created.append(tag)
        tag_id = str(tag.id)
        if tag_id not in ids:
            ids.append(tag_id)
    return ids, created


def find_checklist_item(task: TaskData, reference: str) -> Checklist:
    item = match_one(
        task.checklist,
        reference,
        ids=lambda entry: [str(entry.id)],
        text=lambda entry: entry.text,
        describe=lambda entry: f"'{entry.text}' (id {entry.id})",
        kind="checklist item",
    )
    if item is None:
        listing = "; ".join(f"'{entry.text}' (id {entry.id})" for entry in task.checklist)
        raise ToolInputError(
            f"No checklist item of '{task.text}' matches {reference!r}. "
            f"Items: {listing or 'the checklist is empty'}."
        )
    return item
