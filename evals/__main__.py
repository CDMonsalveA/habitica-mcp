"""Command line for the eval suite.

uv run python -m evals list
uv run python -m evals run --driver ollama --model qwen3:8b --split dev
uv run python -m evals run --driver claude_code --model sonnet --split all --repeat 2
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp import web

from habitica_mcp import __version__
from tests.fake_habitica import FakeHabitica

from .cases import DEFAULT_NOW, DEFAULT_TIMEZONE_OFFSET, Case, load_cases
from .drivers import ClaudeCodeDriver, Driver, OllamaDriver, ServerSpec
from .report import render_markdown, summarize
from .scoring import CaseResult, score

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = Path(__file__).with_name("results")
SYSTEM_CONTEXT = (
    "Session context: today is Wednesday, September 16, 2026, 09:00 in the user's time zone "
    "(UTC-5). Use this date, not any other date you may know."
)


def _seed_with_defaults(case: Case) -> dict[str, Any]:
    seed = copy.deepcopy(case.seed)
    preferences = seed.setdefault("user", {}).setdefault("preferences", {})
    preferences.setdefault("timezoneOffset", DEFAULT_TIMEZONE_OFFSET)
    return seed


async def run_suite(driver: Driver, cases: Sequence[Case], repeat: int) -> list[CaseResult]:
    fake = FakeHabitica(now=lambda: DEFAULT_NOW)
    runner = web.AppRunner(fake.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    host, port = runner.addresses[0][:2]
    server = ServerSpec(
        command=sys.executable,
        args=["-m", "habitica_mcp"],
        env={
            "HABITICA_USER_ID": fake.user_id,
            "HABITICA_API_TOKEN": fake.api_token,
            "HABITICA_BASE_URL": f"http://{host}:{port}",
            "HABITICA_MCP_NOW": DEFAULT_NOW.isoformat(),
        },
        cwd=str(PROJECT_ROOT),
    )
    results: list[CaseResult] = []
    try:
        for case in cases:
            for attempt in range(1, repeat + 1):
                fake.reset(_seed_with_defaults(case))
                trace = await driver.run(case.prompt, server=server, system_context=SYSTEM_CONTEXT)
                result = score(case, trace, fake)
                results.append(result)
                status = "pass" if result.passed else ("UNSAFE" if result.unsafe else "FAIL")
                suffix = f" #{attempt}" if repeat > 1 else ""
                details = f" - {'; '.join(result.failures)}" if result.failures else ""
                print(
                    f"[{status:6}] {case.id}{suffix}: {result.tool_calls} calls, "
                    f"{result.duration_s}s{details}",
                    flush=True,
                )
    finally:
        await runner.cleanup()
    return results


def _build_driver(args: argparse.Namespace) -> Driver:
    if args.driver == "claude_code":
        return ClaudeCodeDriver(args.model, max_turns=args.max_turns, timeout_seconds=args.timeout)
    return OllamaDriver(
        args.model,
        host=args.ollama_host,
        max_turns=args.max_turns,
        timeout_seconds=args.timeout,
        num_ctx=args.num_ctx,
    )


def _run(args: argparse.Namespace) -> int:
    cases = load_cases(split=args.split, ids=args.case)
    if not cases:
        print("No eval cases selected.", file=sys.stderr)
        return 2
    driver = _build_driver(args)
    started = datetime.now(UTC)
    results = asyncio.run(run_suite(driver, cases, args.repeat))
    run_id = (
        f"{started:%Y%m%dT%H%M%SZ}-{driver.name}-{args.model.replace(':', '_').replace('/', '_')}"
    )
    meta = {
        "run_id": run_id,
        "driver": driver.name,
        "model": args.model,
        "resolved_models": sorted({r.model for r in results if r.model}),
        "split": args.split,
        "repeat": args.repeat,
        "server_version": __version__,
        "isolated_claude_config": getattr(driver, "isolated", None),
        "started_at": started.isoformat(),
    }
    summary = summarize(results)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{run_id}.json").write_text(
        json.dumps(
            {"meta": meta, "summary": summary, "results": [r.to_dict() for r in results]},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    markdown = render_markdown(meta, summary, results)
    (out_dir / f"{run_id}.md").write_text(markdown, encoding="utf-8")
    print("\n" + markdown)
    print(f"Saved to {out_dir / run_id}.json and .md")
    return 0 if summary["unsafe_actions"] == 0 else 1


def _list(args: argparse.Namespace) -> int:
    for case in load_cases(split=args.split):
        print(f"{case.id:34} {case.category:7} {case.split:8} {case.lang}  {case.prompt}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="show the eval cases")
    listing.add_argument("--split", choices=["dev", "heldout", "all"], default="all")
    listing.set_defaults(handler=_list)

    run = commands.add_parser("run", help="run eval cases against a model")
    run.add_argument("--driver", choices=["claude_code", "ollama"], required=True)
    run.add_argument("--model", required=True, help="e.g. haiku, sonnet, opus or an Ollama model")
    run.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    run.add_argument("--case", action="append", help="run only this case id (repeatable)")
    run.add_argument("--repeat", type=int, default=1, help="runs per case, to measure variance")
    run.add_argument("--max-turns", type=int, default=12)
    run.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="seconds per Ollama request or per Claude Code case (raise it for CPU-only models)",
    )
    run.add_argument("--num-ctx", type=int, default=16_384, help="Ollama context window in tokens")
    run.add_argument(
        "--ollama-host", default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    )
    run.add_argument("--out", default=str(RESULTS_DIR))
    run.set_defaults(handler=_run)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
