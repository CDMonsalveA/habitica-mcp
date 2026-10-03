"""Run the fake Habitica API as a standalone server (used by evals).

uv run python -m tests.fake_habitica --port 8765 [--seed seed.json] [--now 2026-09-16T14:00:00Z]
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from aiohttp import web

from .server import FakeHabitica


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--seed", type=Path, help="JSON seed file")
    parser.add_argument("--now", help="freeze the clock (ISO 8601 with offset)")
    args = parser.parse_args()

    frozen = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else None
    fake = FakeHabitica(now=(lambda: frozen) if frozen else None)
    if args.seed:
        fake.reset(json.loads(args.seed.read_text(encoding="utf-8")))
    web.run_app(fake.app(), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
