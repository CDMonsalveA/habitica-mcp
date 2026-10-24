"""Command-line entry point: ``habitica-mcp`` runs the server over stdio."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from . import __version__
from .config import Settings
from .const import APP_NAME
from .errors import HabiticaMCPError
from .state import AppState


def _load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        problems = "\n".join(
            f"  - {'.'.join(str(p) for p in error['loc']) or 'settings'}: {error['msg']}"
            for error in exc.errors()
        )
        print(f"{APP_NAME}: invalid configuration\n{problems}", file=sys.stderr)
        raise SystemExit(2) from None


async def check_connection(settings: Settings) -> int:
    """Validate credentials with one cheap request. Returns a process exit code."""
    state = AppState(settings)
    try:
        gateway = await state.gateway()
        user = await gateway.get_user(("profile.name", "stats.lvl"))
    except HabiticaMCPError as exc:
        print(f"{APP_NAME}: {exc}", file=sys.stderr)
        return 1
    finally:
        await state.aclose()
    name = user.profile.name or "unknown"
    print(
        f"{APP_NAME}: connected to {settings.base_url} as {name} (level {user.stats.lvl}). "
        f"Mode: {settings.mode}."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="MCP server for Habitica. Runs over stdio and is configured with "
        "environment variables (HABITICA_USER_ID, HABITICA_API_TOKEN, ...).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--check",
        action="store_true",
        help="check the configuration and credentials against Habitica, then exit",
    )
    args = parser.parse_args(argv)

    settings = _load_settings()
    logging.basicConfig(
        level=settings.log_level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.check:
        # Profile names may contain emoji that a legacy Windows console cannot encode.
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(errors="replace")
        raise SystemExit(asyncio.run(check_connection(settings)))

    from .server import create_server  # imported late: keeps --version and --check fast

    create_server(settings).run(transport="stdio", show_banner=False)
