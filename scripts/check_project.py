"""Project-wide consistency rules that linters cannot see.

- One version everywhere: ``habitica_mcp.__version__`` and both versions in server.json.
- The registry entry matches the package: PyPI name, and the ``mcp-name`` line in README.md
  that the MCP Registry uses to verify ownership.
- Every tool in the reviewed tool contract is documented in both READMEs.
- Eval case files are valid and have unique ids.
- Every GitHub Action is pinned to a full commit SHA (the repository requires it).

    uv run python scripts/check_project.py
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_LINE = re.compile(r'^__version__ = "([^"]+)"', re.MULTILINE)
USES_LINE = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.MULTILINE)
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def check_versions() -> list[str]:
    match = VERSION_LINE.search(_read("src/habitica_mcp/__init__.py"))
    if match is None:
        return ["src/habitica_mcp/__init__.py has no __version__ line"]
    version = match.group(1)
    server = json.loads(_read("server.json"))
    found: list[str] = []
    for label, value in (
        ("server.json version", server.get("version")),
        ("server.json packages[0].version", server["packages"][0].get("version")),
    ):
        if value != version:
            found.append(f"{label} is {value!r} but __version__ is {version!r}")
    return found


def check_registry_entry() -> list[str]:
    project = tomllib.loads(_read("pyproject.toml"))["project"]
    server = json.loads(_read("server.json"))
    found: list[str] = []
    if server["packages"][0]["identifier"] != project["name"]:
        found.append("server.json packages[0].identifier must equal the PyPI project name")
    if f"mcp-name: {server['name']}" not in _read("README.md"):
        found.append(f"README.md must contain 'mcp-name: {server['name']}' (registry ownership)")
    return found


def check_tools_documented() -> list[str]:
    tools = json.loads(_read("tests/protocol/tools_contract.json"))
    found: list[str] = []
    for readme in ("README.md", "README.es.md"):
        text = _read(readme)
        found.extend(f"{readme} does not document {name}" for name in tools if name not in text)
    return found


def check_eval_cases() -> list[str]:
    sys.path.insert(0, str(ROOT))
    from evals.cases import load_cases

    try:
        cases = load_cases(split="all")
    except Exception as exc:  # report any schema or duplicate-id problem
        return [f"eval cases: {exc}"]
    return [] if cases else ["no eval cases found"]


def check_actions_pinned() -> list[str]:
    found: list[str] = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        for action in USES_LINE.findall(workflow.read_text(encoding="utf-8")):
            if not action.startswith("./") and not PINNED.match(action):
                found.append(f"{workflow.name}: '{action}' must be pinned to a commit SHA")
    return found


CHECKS: tuple[Callable[[], list[str]], ...] = (
    check_versions,
    check_registry_entry,
    check_tools_documented,
    check_eval_cases,
    check_actions_pinned,
)


def main() -> int:
    found = [problem for check in CHECKS for problem in check()]
    for problem in found:
        print(f"project rule: {problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
