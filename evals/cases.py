"""Eval case files (YAML) and their schema."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

CASES_DIR = Path(__file__).with_name("cases")
# Every case runs on Wednesday 2026-09-16 at 09:00 in the user's UTC-5 time zone.
DEFAULT_NOW = datetime(2026, 9, 16, 14, 0, tzinfo=UTC)
DEFAULT_TIMEZONE_OFFSET = 300


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskSelector(_Strict):
    type: Literal["habit", "daily", "todo", "reward"] | None = None
    text: str | None = None
    text_contains: str | None = None


class TaskExpectation(_Strict):
    where: TaskSelector
    has: dict[str, Any] = Field(default_factory=dict)


class ToolExpectation(_Strict):
    required: list[str] = Field(default_factory=list, description="Each must be called")
    any_of: list[str] = Field(default_factory=list, description="At least one must be called")
    forbidden: list[str] = Field(default_factory=list)
    max_calls: int | None = None


class AnswerExpectation(_Strict):
    all_of: list[str] = Field(default_factory=list)
    any_of: list[str] = Field(default_factory=list)
    none_of: list[str] = Field(default_factory=list)


class Expectations(_Strict):
    tasks: list[TaskExpectation] = Field(default_factory=list)
    no_tasks: list[TaskSelector] = Field(default_factory=list)
    no_writes: bool = False
    gold: float | None = None
    tools: ToolExpectation = Field(default_factory=ToolExpectation)
    answer: AnswerExpectation = Field(default_factory=AnswerExpectation)


class Case(_Strict):
    id: str
    category: Literal["read", "create", "score", "edit", "safety"]
    split: Literal["dev", "heldout"] = "dev"
    lang: Literal["es", "en"]
    prompt: str
    description: str = ""
    seed: dict[str, Any] = Field(default_factory=dict)
    expect: Expectations

    @property
    def safety(self) -> bool:
        return self.expect.no_writes or bool(self.expect.no_tasks)


def load_cases(
    directory: Path = CASES_DIR,
    *,
    split: str | None = None,
    ids: list[str] | None = None,
) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(directory.glob("*.yaml")):
        documents = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for document in documents if isinstance(documents, list) else [documents]:
            cases.append(Case.model_validate(document))
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"Duplicate eval case id: {case.id}")
        seen.add(case.id)
    if split and split != "all":
        cases = [c for c in cases if c.split == split]
    if ids:
        cases = [c for c in cases if c.id in ids]
    return cases
