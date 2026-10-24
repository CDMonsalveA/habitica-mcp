"""The pre-commit checks themselves (secrets, commit messages, project rules)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_commit_msg, check_project, check_secrets

REAL_LOOKING = "1b4e28ba-2fa1-41d2-883f-0016d3cca427"
PLACEHOLDER = "00000000-0000-4000-8000-0000000000aa"


@pytest.mark.parametrize(
    "line",
    [
        f'HABITICA_API_TOKEN="{REAL_LOOKING}"',
        f'"x-api-key": "{REAL_LOOKING}"',
        f"api_token = '{REAL_LOOKING}'",
        "key = 'sk-ant-api03-" + "A" * 30 + "'",
        "token = 'ghp_" + "a" * 36 + "'",
        "AKIA" + "ABCDEFGHIJKLMNOP",
    ],
)
def test_secret_patterns_are_reported(line: str) -> None:
    assert check_secrets.scan_text("f.py", line), line


@pytest.mark.parametrize(
    "line",
    [
        f'DEFAULT_API_TOKEN = "{PLACEHOLDER}"',
        f'MAINTAINER_USER_ID = "{REAL_LOOKING}"',  # user ids are public
        "HABITICA_API_TOKEN=your-api-token",
        f'api_token = "{REAL_LOOKING}"  # secrets: allow',
        "api_token=settings.api_token",
    ],
)
def test_ids_placeholders_and_allowed_lines_pass(line: str) -> None:
    assert check_secrets.scan_text("f.py", line) == []


def test_values_of_sensitive_variables_are_found_without_being_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "my-very-secret-value-123456"
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"HABITICA_API_TOKEN={secret}\nOTHER=x\n", encoding="utf-8")
    leaked = tmp_path / "notes.md"
    leaked.write_text(f"token is {secret}\n", encoding="utf-8")

    values = check_secrets.secret_values({"ANTHROPIC_API_KEY": "short"}, dotenv)
    findings = check_secrets.check_files([str(leaked), str(dotenv)], values)

    assert values == {secret}
    assert [f.rule for f in findings] == [
        "contains the value of a sensitive variable",
        ".env files must never be committed",
    ]
    assert secret not in "".join(str(f) for f in findings)
    assert check_secrets.is_env_file(Path(".env.example")) is False


def test_check_secrets_main_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    clean = tmp_path / "clean.py"
    clean.write_text("x = 1\n", encoding="utf-8")
    dirty = tmp_path / "dirty.py"
    dirty.write_text(f'api_key = "{REAL_LOOKING}"\n', encoding="utf-8")

    assert check_secrets.main([str(clean)]) == 0
    assert check_secrets.main([str(dirty)]) == 1


@pytest.mark.parametrize(
    "message",
    [
        "feat: add a tool",
        "fix(gateway): honor Retry-After\n\nDetails",
        "feat(tools)!: rename a parameter",
        "chore(main): release 0.2.0",
        "Merge pull request #3 from x/y",
        "# comment\nci: pin actions",
    ],
)
def test_conventional_commits_pass(message: str) -> None:
    assert check_commit_msg.problems(message) == []


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Add a tool", "type(scope): description"),
        ("feature: add a tool", "unknown type 'feature'"),
        ("fix: " + "x" * 120, "max 100"),
        ("# only a comment", "empty"),
    ],
)
def test_bad_commit_messages_are_explained(message: str, expected: str) -> None:
    assert expected in " ".join(check_commit_msg.problems(message))


def test_commit_msg_main(tmp_path: Path) -> None:
    good, bad = tmp_path / "good", tmp_path / "bad"
    good.write_text("docs: explain evals\n", encoding="utf-8")
    bad.write_text("updated stuff\n", encoding="utf-8")
    assert check_commit_msg.main([str(good)]) == 0
    assert check_commit_msg.main([str(bad)]) == 1


def test_the_repository_follows_the_project_rules() -> None:
    assert check_project.main() == 0
