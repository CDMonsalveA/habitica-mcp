"""Keep personal and compromising data out of Habitica.

Habitica advises against storing sensitive information in tasks: they are not encrypted
end to end, staff can read them, and challenge or party tasks are shared. Every write goes
through :func:`guard_body` before it leaves the server, so no tool can skip the check.

Detection is deliberately conservative (regular expressions plus a Luhn check for cards):
it catches the data that does real harm when leaked, not every personal detail. Telling
the model to write anonymously (server instructions) is the second layer.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

PrivacyMode = Literal["block", "redact", "off"]
REDACTED = "[redacted]"
# Body fields that carry user-written text; everything else is ids, numbers and flags.
_TEXT_FIELDS = ("text", "notes", "name")


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    masked: str


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _is_card(value: str) -> bool:
    digits = _digits(value)
    return 13 <= len(digits) <= 19 and _luhn(digits)


def _is_phone(value: str) -> bool:
    """10-15 digits (8+ with a country code) in at most 4-5 groups, one of 3+ digits.

    The grouping rule keeps lists of small numbers ("series 10 12 15 20 25") out.
    """
    digits = _digits(value)
    groups = re.findall(r"\d+", value)
    international = value.startswith("+")
    if len(groups) > (5 if international else 4):
        return False
    if len(groups) > 1 and max(len(group) for group in groups) < 3:
        return False
    return 10 <= len(digits) <= 15 or (international and len(digits) >= 8)


def _always(_value: str) -> bool:
    return True


_KEYWORD_VALUE = r"\s*(?:[:=#]|es|is|n[°ºo.]*)?\s*"

# (kind, pattern, extra check). Order matters: earlier kinds claim their spans first.
_RULES: tuple[tuple[str, re.Pattern[str], Callable[[str], bool]], ...] = (
    (
        "a link with an access key",
        re.compile(
            r"https?://\S*[?&](?:token|key|apikey|api_key|sig|signature|access_token|code)=\S+",
            re.IGNORECASE,
        ),
        _always,
    ),
    (
        "a password or secret",
        # "clave: x9!" or "my password is hunter2": an explicit separator, or "es"/"is"
        # followed by something password-like, so "la clave para el éxito" passes.
        re.compile(
            r"\b(?:password|passwd|contrase[ñn]a|clave|pin|token|api[ _-]?key|secret|secreto)"
            r"(?:\s*[:=]\s*\S{3,}|\s+(?:es|is)\s+(?=\S*[\d@#$%&*!?])\S{4,})",
            re.IGNORECASE,
        ),
        _always,
    ),
    (
        "an API key or access token",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
            r"|xox[abprs]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.\S+)"
        ),
        _always,
    ),
    ("an email address", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), _always),
    (
        "an IBAN",
        re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b"),
        _always,
    ),
    ("a card number", re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)"), _is_card),
    (
        "an identity document number",
        re.compile(
            r"\b(?:c[ée]dula|c\.?c\.?|dni|nit|rut|curp|pasaporte|passport|ssn|nie|rfc)"
            + _KEYWORD_VALUE
            + r"[A-Z0-9][A-Z0-9.\- ]{4,18}\d",
            re.IGNORECASE,
        ),
        _always,
    ),
    (
        "an account number",
        re.compile(
            # "cuenta de ahorros 123-456789-01": up to two words between keyword and number
            r"\b(?:cuenta|account|acct|cta)\b(?:\s+[^\W\d]+){0,2}"
            + _KEYWORD_VALUE
            + r"\d[\d \-]{5,}\d",
            re.IGNORECASE,
        ),
        _always,
    ),
    ("a phone number", re.compile(r"(?<![\w-])\+?\(?\d[\d ()\-.]{7,}\d(?![\w-])"), _is_phone),
)


def _mask(value: str) -> str:
    value = value.strip()
    return "***" + value[-2:] if len(value) > 4 else "***"


def _scan(text: str) -> list[tuple[int, int, Finding]]:
    spans: list[tuple[int, int, Finding]] = []
    for kind, pattern, check in _RULES:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < s_end and s_start < end for s_start, s_end, _ in spans):
                continue
            if check(match.group()):
                spans.append((start, end, Finding(kind, _mask(match.group()))))
    return sorted(spans, key=lambda span: span[0])


def find_sensitive(text: str) -> list[Finding]:
    """Sensitive data in a piece of text, masked so it can be reported safely."""
    return [finding for _, _, finding in _scan(text)]


def redact(text: str) -> str:
    """Replace every sensitive match with ``[redacted]``."""
    for start, end, _ in reversed(_scan(text)):
        text = text[:start] + REDACTED + text[end:]
    return text


def _checklist(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Checklist items of a create body (``[{"text": ...}, ...]``)."""
    items = body.get("checklist")
    if not isinstance(items, list):
        return []
    return [cast(dict[str, Any], item) for item in cast(list[Any], items) if isinstance(item, dict)]


def _texts(body: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(field, value) for every user-written string in a write body."""
    found = [(field, value) for field in _TEXT_FIELDS if isinstance(value := body.get(field), str)]
    found += [
        ("checklist", text)
        for item in _checklist(body)
        if isinstance(text := item.get("text"), str)
    ]
    return found


def scan_body(body: Mapping[str, Any]) -> list[tuple[str, Finding]]:
    return [(field, f) for field, value in _texts(body) for f in find_sensitive(value)]


def redact_body(body: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = dict(body)
    for field in _TEXT_FIELDS:
        if isinstance(value := cleaned.get(field), str):
            cleaned[field] = redact(value)
    if "checklist" in cleaned:
        cleaned["checklist"] = [
            {**item, "text": redact(text)} if isinstance(text := item.get("text"), str) else item
            for item in _checklist(cleaned)
        ]
    return cleaned
