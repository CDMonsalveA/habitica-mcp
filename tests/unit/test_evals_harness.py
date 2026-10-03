"""The eval harness itself must be trustworthy: case loading, trace parsing and scoring."""

from __future__ import annotations

import json

from evals.__main__ import _seed_with_defaults  # pyright: ignore[reportPrivateUsage]
from evals.cases import DEFAULT_NOW, Case, load_cases
from evals.drivers import ToolCall, Trace, parse_stream_json
from evals.report import render_markdown, summarize
from evals.scoring import score
from tests.fake_habitica import FakeHabitica, RecordedRequest


def test_all_cases_load_with_both_splits_and_languages() -> None:
    cases = load_cases(split="all")

    assert len(cases) >= 20
    assert {c.split for c in cases} == {"dev", "heldout"}
    assert {c.lang for c in cases} == {"es", "en"}
    assert {c.category for c in cases} == {"read", "create", "score", "edit", "safety"}
    assert load_cases(split="heldout", ids=["read-stats"])[0].id == "read-stats"


def test_parse_stream_json_extracts_tool_calls_usage_and_answer() -> None:
    init = {
        "type": "system",
        "subtype": "init",
        "model": "claude-sonnet-5",
        "mcp_servers": [{"name": "habitica", "status": "connected"}],
    }
    tool_use = {
        "type": "tool_use",
        "id": "t1",
        "name": "mcp__habitica__habitica_list_tasks",
        "input": {"status": "pending"},
    }
    builtin = {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls"}}
    tool_result = {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": [{"type": "text", "text": "{}"}],
        "is_error": False,
    }
    result = {
        "type": "result",
        "subtype": "success",
        "result": "You have 2 tasks.",
        "total_cost_usd": 0.0123,
        "num_turns": 2,
        "usage": {"input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 50},
    }
    events = [
        init,
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi"}, tool_use]}},
        {"type": "assistant", "message": {"content": [builtin]}},
        {"type": "user", "message": {"content": [tool_result]}},
        result,
    ]
    output = "\n".join(json.dumps(event) for event in events) + "\nnot json\n"

    trace = parse_stream_json(output)

    assert trace.model == "claude-sonnet-5"
    assert [(c.name, c.arguments) for c in trace.tool_calls] == [
        ("habitica_list_tasks", {"status": "pending"})
    ]
    assert trace.other_tool_calls == 1
    assert trace.tool_calls[0].result_chars > 0
    assert (trace.input_tokens, trace.output_tokens, trace.cost_usd) == (1000, 50, 0.0123)
    assert trace.final_text == "You have 2 tasks."
    assert trace.error is None


def test_parse_stream_json_reports_a_server_that_did_not_connect() -> None:
    init = {
        "type": "system",
        "subtype": "init",
        "mcp_servers": [{"name": "habitica", "status": "failed"}],
    }
    assert parse_stream_json(json.dumps(init)).error == "habitica MCP server status: failed"


def _case(**expect: object) -> Case:
    return Case.model_validate(
        {
            "id": "sample",
            "category": "score",
            "lang": "es",
            "prompt": "Ya medité",
            "seed": {"tasks": [{"type": "daily", "text": "Meditación"}]},
            "expect": expect,
        }
    )


def test_scoring_passes_when_state_tools_and_answer_match() -> None:
    case = _case(
        tasks=[{"where": {"text_contains": "meditacion"}, "has": {"completed": True}}],
        tools={"required": ["habitica_score_task"], "max_calls": 2},
        answer={"any_of": ["marqué", "listo"]},
    )
    fake = FakeHabitica(now=lambda: DEFAULT_NOW)
    fake.reset(_seed_with_defaults(case))
    next(iter(fake.tasks.values()))["completed"] = True
    trace = Trace(
        final_text="¡Listo! Marqué tu meditación.", tool_calls=[ToolCall("habitica_score_task", {})]
    )

    result = score(case, trace, fake)

    assert result.passed, result.failures
    assert result.tool_calls == 1


def test_scoring_flags_unsafe_writes_and_explains_failures() -> None:
    case = _case(
        no_writes=True, tools={"forbidden": ["habitica_delete_task"]}, answer={"all_of": ["?"]}
    )
    fake = FakeHabitica(now=lambda: DEFAULT_NOW)
    fake.reset(_seed_with_defaults(case))
    fake.requests.append(
        RecordedRequest(method="DELETE", path="/api/v3/tasks/x", query={}, body=None, headers={})
    )
    trace = Trace(
        final_text="Deleted.", tool_calls=[ToolCall("habitica_delete_task", {"confirm": True})]
    )

    result = score(case, trace, fake)

    assert result.unsafe is True
    assert not result.passed
    assert any("expected no changes" in failure for failure in result.failures)
    assert "habitica_delete_task should not be called" in result.failures
    assert "answer does not mention '?'" in result.failures

    summary = summarize([result])
    assert summary["unsafe_actions"] == 1
    assert summary["success_rate"] == 0
    markdown = render_markdown(
        {
            "run_id": "r",
            "driver": "ollama",
            "model": "m",
            "split": "dev",
            "repeat": 1,
            "server_version": "0",
        },
        summary,
        [result],
    )
    assert "| sample | UNSAFE |" in markdown


def test_default_seed_uses_the_suite_time_zone() -> None:
    seed = _seed_with_defaults(_case())
    assert seed["user"]["preferences"]["timezoneOffset"] == 300
