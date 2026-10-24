"""Require Conventional Commit messages; release-please builds versions and the changelog
from them (feat -> minor, fix -> patch, ``!`` or BREAKING CHANGE -> major).

    python scripts/check_commit_msg.py .git/COMMIT_EDITMSG
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

TYPES = (
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
)
SUBJECT = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[\w./ -]+)\))?(?P<breaking>!)?: (?P<text>\S.*)$"
)
MAX_SUBJECT_LENGTH = 100
# Messages git or GitHub generate, or that will be squashed away.
EXEMPT_PREFIXES = ("Merge ", 'Revert "', "fixup! ", "squash! ", "amend! ")


def problems(message: str) -> list[str]:
    lines = [line for line in message.splitlines() if not line.startswith("#")]
    subject = next((line.strip() for line in lines if line.strip()), "")
    if not subject:
        return ["the commit message is empty"]
    if subject.startswith(EXEMPT_PREFIXES):
        return []
    found: list[str] = []
    match = SUBJECT.match(subject)
    if match is None:
        found.append(
            "the first line must look like 'type(scope): description', "
            f"with type one of: {', '.join(TYPES)}"
        )
    elif match.group("type") not in TYPES:
        found.append(f"unknown type '{match.group('type')}'; use one of: {', '.join(TYPES)}")
    if len(subject) > MAX_SUBJECT_LENGTH:
        found.append(f"the first line has {len(subject)} characters (max {MAX_SUBJECT_LENGTH})")
    return found


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    message = Path(args[0]).read_text(encoding="utf-8") if args else ""
    found = problems(message)
    for problem in found:
        print(f"commit message: {problem}", file=sys.stderr)
    if found:
        print("Example: 'feat(tools): add habit history to habitica_get_task'", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
