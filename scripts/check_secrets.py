"""Block commits that contain credentials.

Habitica API tokens are plain UUIDs, so generic scanners cannot tell them from the many
ids in this code base. This check therefore looks for:

1. the actual values of sensitive variables in the environment or in a local ``.env``
   file (the most likely leak: pasting your own token into a test or a doc);
2. a UUID assigned to a token/key-like name, unless it is an obvious placeholder
   (``00000000-...``);
3. well-known token formats (Anthropic, GitHub, PyPI, AWS);
4. ``.env`` files themselves.

Secrets are never printed: findings show the file, the line and the rule only.
Add ``# secrets: allow`` to a line to accept a reviewed false positive.

    python scripts/check_secrets.py FILE...
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

SENSITIVE_VARIABLES = (
    "HABITICA_API_TOKEN",
    "HABITICA_TEST_API_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "PYPI_TOKEN",
)
ALLOW_PRAGMA = "secrets: allow"
PLACEHOLDER_UUID_PREFIX = "00000000-"
MIN_SECRET_LENGTH = 16

UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
TOKEN_ASSIGNMENT = re.compile(
    r"(?i)(?:api[_-]?(?:token|key)|x-api-key|secret|password|"
    r"habitica_[a-z_]*token)[\"']?\s*[:=]\s*[\"']?(" + UUID + ")"
)
KNOWN_FORMATS = {
    "Anthropic API key or OAuth token": re.compile(r"sk-ant-[a-z0-9]{2,10}-[A-Za-z0-9_\-]{20,}"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})"),
    "PyPI token": re.compile(r"pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_\-]{20,}"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}"


def secret_values(environ: Mapping[str, str], dotenv: Path | None = None) -> set[str]:
    """Values of sensitive variables from the environment and an optional .env file."""
    values = {environ[name] for name in SENSITIVE_VARIABLES if environ.get(name)}
    if dotenv is not None and dotenv.is_file():
        for raw in dotenv.read_text(encoding="utf-8", errors="replace").splitlines():
            name, sep, value = raw.partition("=")
            if sep and name.strip() in SENSITIVE_VARIABLES:
                values.add(value.strip().strip("\"'"))
    return {value for value in values if len(value) >= MIN_SECRET_LENGTH}


def scan_text(path: str, text: str, secrets: Iterable[str] = ()) -> list[Finding]:
    findings: list[Finding] = []
    known = [secret for secret in secrets if secret]
    for number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_PRAGMA in line:
            continue
        if any(secret in line for secret in known):
            findings.append(Finding(path, number, "contains the value of a sensitive variable"))
            continue
        for match in TOKEN_ASSIGNMENT.finditer(line):
            if not match.group(1).startswith(PLACEHOLDER_UUID_PREFIX):
                findings.append(
                    Finding(path, number, "a real-looking UUID is assigned to a token or key")
                )
        for rule, pattern in KNOWN_FORMATS.items():
            if pattern.search(line):
                findings.append(Finding(path, number, rule))
    return findings


def is_env_file(path: Path) -> bool:
    name = path.name
    return (name == ".env" or name.startswith(".env.") or name.endswith(".env")) and (
        name != ".env.example"
    )


def check_files(paths: Sequence[str], secrets: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    secrets = list(secrets)
    for name in paths:
        path = Path(name)
        if is_env_file(path):
            findings.append(Finding(name, 0, ".env files must never be committed"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: other hooks handle large or binary files
        findings.extend(scan_text(name, text, secrets))
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    paths = list(sys.argv[1:] if argv is None else argv)
    findings = check_files(paths, secret_values(os.environ, Path(".env")))
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        print(
            f"\n{len(findings)} possible secret(s) found. Remove them (and rotate any real "
            f"credential), or mark a reviewed false positive with '# {ALLOW_PRAGMA}'.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
