"""Aggregate case results into the metrics tracked across releases."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from .scoring import CaseResult


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(fraction * len(ordered)) - 1, 0)]


def summarize(results: Sequence[CaseResult]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    calls = [r.tool_calls for r in results]
    durations = [r.duration_s for r in results]
    total_calls = sum(calls)
    per_group: dict[str, dict[str, dict[str, int]]] = {"category": {}, "split": {}, "lang": {}}
    for dimension in per_group:
        buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"cases": 0, "passed": 0})
        for result in results:
            bucket = buckets[str(getattr(result, dimension))]
            bucket["cases"] += 1
            bucket["passed"] += int(result.passed)
        per_group[dimension] = dict(sorted(buckets.items()))
    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    return {
        "cases": total,
        "passed": passed,
        "success_rate": round(passed / total, 3) if total else None,
        "unsafe_actions": sum(1 for r in results if r.unsafe),
        "run_errors": sum(1 for r in results if r.error),
        "tool_calls_median": statistics.median(calls) if calls else None,
        "tool_calls_p95": _percentile(calls, 0.95),
        "tool_error_rate": round(sum(r.tool_errors for r in results) / total_calls, 3)
        if total_calls
        else 0.0,
        "tool_result_chars_per_call": round(sum(r.tool_result_chars for r in results) / total_calls)
        if total_calls
        else 0,
        "habitica_requests_per_case": round(sum(r.habitica_requests for r in results) / total, 2)
        if total
        else None,
        "duration_s_median": round(statistics.median(durations), 2) if durations else None,
        "duration_s_p95": _percentile(durations, 0.95),
        "input_tokens": sum(r.input_tokens or 0 for r in results),
        "output_tokens": sum(r.output_tokens or 0 for r in results),
        "cost_usd_estimate": round(sum(costs), 4) if costs else None,
        **per_group,
    }


def render_markdown(
    meta: dict[str, Any], summary: dict[str, Any], results: Sequence[CaseResult]
) -> str:
    lines = [
        f"# Eval run {meta['run_id']}",
        "",
        f"- Driver: `{meta['driver']}` · model: `{meta['model']}` · split: `{meta['split']}` · "
        f"repeats: {meta['repeat']}",
        f"- Server: habitica-mcp {meta['server_version']}",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    for key in (
        "success_rate",
        "passed",
        "cases",
        "unsafe_actions",
        "run_errors",
        "tool_calls_median",
        "tool_calls_p95",
        "tool_error_rate",
        "tool_result_chars_per_call",
        "habitica_requests_per_case",
        "duration_s_median",
        "duration_s_p95",
        "input_tokens",
        "output_tokens",
        "cost_usd_estimate",
    ):
        lines.append(f"| {key} | {summary[key]} |")
    lines += ["", "| Category | Passed |", "|---|---|"]
    for category, bucket in summary["category"].items():
        lines.append(f"| {category} | {bucket['passed']}/{bucket['cases']} |")
    lines += ["", "| Case | Result | Calls | Tools | Failures |", "|---|---|---|---|---|"]
    for result in results:
        status = "pass" if result.passed else ("UNSAFE" if result.unsafe else "fail")
        failures = "; ".join(result.failures).replace("|", "\\|")
        tools = ", ".join(t.removeprefix("habitica_") for t in result.tools_used)
        lines.append(
            f"| {result.case_id} | {status} | {result.tool_calls} | {tools} | {failures} |"
        )
    return "\n".join(lines) + "\n"
