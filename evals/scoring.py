"""Turn a trace and the final Habitica state into a pass/fail result with metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from habitica_mcp.resolve import normalize
from tests.fake_habitica import FakeHabitica

from .cases import Case, TaskSelector
from .drivers.base import Trace

WEEKDAY_KEYS = {
    "mon": "m",
    "tue": "t",
    "wed": "w",
    "thu": "th",
    "fri": "f",
    "sat": "s",
    "sun": "su",
}
UNORDERED_KEYS = {"tags", "weekdays", "checklist_texts", "checklist_done"}
FINAL_TEXT_CHARS = 1500


@dataclass
class CaseResult:
    case_id: str
    category: str
    split: str
    lang: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    unsafe: bool = False
    tool_calls: int = 0
    tool_errors: int = 0
    tools_used: list[str] = field(default_factory=list)
    tool_result_chars: int = 0
    habitica_requests: int = 0
    habitica_writes: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    duration_s: float = 0.0
    turns: int | None = None
    model: str | None = None
    error: str | None = None
    final_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _task_matches(task: dict[str, Any], selector: TaskSelector) -> bool:
    text = normalize(str(task.get("text", "")))
    if selector.type is not None and task.get("type") != selector.type:
        return False
    if selector.text is not None and text != normalize(selector.text):
        return False
    return selector.text_contains is None or normalize(selector.text_contains) in text


def _field(fake: FakeHabitica, task: dict[str, Any], key: str) -> Any:
    tag_names = {tag["id"]: tag["name"] for tag in fake.user["tags"]}
    if key == "due_date":
        raw = task.get("date")
        if not raw:
            return None
        instant = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return fake.local_date(instant).isoformat()
    if key == "tags":
        return [tag_names.get(tag_id, tag_id) for tag_id in task.get("tags", [])]
    if key == "checklist_texts":
        return [item["text"] for item in task.get("checklist", [])]
    if key == "checklist_done":
        return [item["text"] for item in task.get("checklist", []) if item.get("completed")]
    if key == "weekdays":
        repeat = task.get("repeat", {})
        return [day for day, short in WEEKDAY_KEYS.items() if repeat.get(short)]
    return task.get(key)


def _same(actual: Any, expected: Any, key: str) -> bool:
    if isinstance(expected, bool) or expected is None:
        return actual is expected or actual == expected
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return abs(float(actual) - float(expected)) < 0.01
    if isinstance(expected, str):
        return isinstance(actual, str) and normalize(actual) == normalize(expected)
    if isinstance(expected, list) and isinstance(actual, list):
        left = [normalize(str(item)) for item in actual]
        right = [normalize(str(item)) for item in expected]
        return sorted(left) == sorted(right) if key in UNORDERED_KEYS else left == right
    return bool(actual == expected)


def score(case: Case, trace: Trace, fake: FakeHabitica) -> CaseResult:
    expect = case.expect
    failures: list[str] = []
    if trace.error:
        failures.append(f"run error: {trace.error}")

    tasks = list(fake.tasks.values())
    for expectation in expect.tasks:
        matches = [t for t in tasks if _task_matches(t, expectation.where)]
        label = expectation.where.model_dump(exclude_none=True)
        if len(matches) != 1:
            failures.append(f"expected one task matching {label}, found {len(matches)}")
            continue
        for key, expected in expectation.has.items():
            actual = _field(fake, matches[0], key)
            if not _same(actual, expected, key):
                failures.append(f"task {label}: {key} is {actual!r}, expected {expected!r}")
    for selector in expect.no_tasks:
        if any(_task_matches(t, selector) for t in tasks):
            failures.append(
                f"a task matching {selector.model_dump(exclude_none=True)} should not exist"
            )

    api_requests = fake.api_requests()
    writes = [r for r in api_requests if r.method != "GET"]
    unsafe = False
    if expect.no_writes and writes:
        unsafe = True
        listing = ", ".join(f"{r.method} {r.path}" for r in writes[:5])
        failures.append(f"expected no changes, but Habitica received: {listing}")
    if expect.gold is not None and abs(float(fake.user["stats"]["gp"]) - expect.gold) > 0.01:
        failures.append(f"gold is {fake.user['stats']['gp']}, expected {expect.gold}")

    names = [call.name for call in trace.tool_calls]
    for tool in expect.tools.required:
        if tool not in names:
            failures.append(f"{tool} was not called")
    if expect.tools.any_of and not set(expect.tools.any_of) & set(names):
        failures.append(f"none of {expect.tools.any_of} was called")
    for tool in expect.tools.forbidden:
        if tool in names:
            failures.append(f"{tool} should not be called")
    if expect.tools.max_calls is not None and len(names) > expect.tools.max_calls:
        failures.append(f"{len(names)} tool calls, expected at most {expect.tools.max_calls}")

    answer = normalize(trace.final_text)
    for needle in expect.answer.all_of:
        if normalize(needle) not in answer:
            failures.append(f"answer does not mention {needle!r}")
    if expect.answer.any_of and not any(normalize(n) in answer for n in expect.answer.any_of):
        failures.append(f"answer mentions none of {expect.answer.any_of}")
    for needle in expect.answer.none_of:
        if normalize(needle) in answer:
            failures.append(f"answer should not mention {needle!r}")

    return CaseResult(
        case_id=case.id,
        category=case.category,
        split=case.split,
        lang=case.lang,
        passed=not failures,
        failures=failures,
        unsafe=unsafe,
        tool_calls=len(names),
        tool_errors=sum(1 for call in trace.tool_calls if call.is_error),
        tools_used=names,
        tool_result_chars=sum(call.result_chars for call in trace.tool_calls),
        habitica_requests=len(api_requests),
        habitica_writes=len(writes),
        input_tokens=trace.input_tokens,
        output_tokens=trace.output_tokens,
        cost_usd=trace.cost_usd,
        duration_s=round(trace.duration_s, 2),
        turns=trace.turns,
        model=trace.model,
        error=trace.error,
        final_text=trace.final_text[:FINAL_TEXT_CHARS],
    )
