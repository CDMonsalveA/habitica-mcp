"""Errors whose messages are meant to be read by the model.

Every subclass of :class:`HabiticaMCPError` carries a message that is safe to show
(no credentials, no stack traces) and tells the model what to do next. They subclass
FastMCP's ``ToolError``, so FastMCP returns the message as a tool error even with
``mask_error_details`` on; any other exception is masked.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, ClassVar, cast

from fastmcp.exceptions import ToolError

# Habitica reuses HTTP 401 for several business rules ("Not Enough Gold", "A task
# belonging to a challenge can't be deleted.", ...). Only these messages mean bad credentials.
_CREDENTIAL_HINTS = ("credential", "authentication", "api token", "apitoken")


class HabiticaMCPError(ToolError):
    """Base class for errors that are reported to the model as tool errors."""

    log_level_default: ClassVar[int] = logging.WARNING

    def __init__(self, message: str) -> None:
        super().__init__(message, log_level=self.log_level_default)


class MissingCredentialsError(HabiticaMCPError):
    """The server was started without Habitica credentials."""

    def __init__(self) -> None:
        super().__init__(
            "Habitica credentials are not configured. Set HABITICA_USER_ID and "
            "HABITICA_API_TOKEN (Habitica > Settings > Site Data) in the MCP server "
            "configuration and restart the server."
        )


class HabiticaConnectionError(HabiticaMCPError):
    """Habitica could not be reached."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Could not reach Habitica ({detail}). Check the network connection or "
            "https://status.habitica.com and try again later."
        )


class RateLimitedError(HabiticaMCPError):
    """The request would exceed Habitica's rate limit."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(
            f"Habitica's rate limit (30 requests per minute) was reached. Wait about "
            f"{max(1, round(retry_after))} seconds before calling Habitica tools again, "
            "and prefer filters over repeated listing."
        )


class HabiticaAPIError(HabiticaMCPError):
    """Habitica answered with an error status."""

    def __init__(
        self,
        status: int,
        error: str | None,
        message: str | None,
        details: Sequence[str] = (),
    ) -> None:
        self.status = status
        self.error = error
        self.api_message = message
        self.details = tuple(details)
        super().__init__(self._describe())

    @classmethod
    def from_payload(cls, status: int, payload: Any) -> HabiticaAPIError:
        """Build the error from Habitica's JSON error body (which may be missing)."""
        body: dict[str, Any] = cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
        error, message = body.get("error"), body.get("message")
        raw_errors = body.get("errors")
        items: list[Any] = cast(list[Any], raw_errors) if isinstance(raw_errors, list) else []
        details = [
            text
            for item in items
            if isinstance(item, dict)
            and isinstance(text := cast(dict[str, Any], item).get("message"), str)
        ]
        return cls(
            status,
            error if isinstance(error, str) else None,
            message if isinstance(message, str) else None,
            details,
        )

    def _describe(self) -> str:
        reason = self.api_message or self.error or "no details"
        if self.details:
            reason = f"{reason} ({'; '.join(self.details)})"
        if self.status == 401:
            if any(hint in reason.lower() for hint in _CREDENTIAL_HINTS):
                return (
                    f"Habitica rejected the credentials: {reason} Check HABITICA_USER_ID "
                    "and HABITICA_API_TOKEN in the server configuration."
                )
            return f"Habitica refused the action: {reason}"
        if self.status == 404:
            return (
                f"Habitica could not find it: {reason} Use habitica_list_tasks or "
                "habitica_manage_tags(action='list') to get valid references."
            )
        if self.status == 403:
            return f"Habitica does not allow this request: {reason}"
        if self.status >= 500:
            return f"Habitica had an internal problem (HTTP {self.status}). Try again later."
        return f"Habitica rejected the request (HTTP {self.status}): {reason}"


class UnexpectedResponseError(HabiticaMCPError):
    """Habitica answered with data this server cannot read (likely an API change)."""

    def __init__(self, what: str) -> None:
        super().__init__(
            f"Habitica returned {what} in an unexpected format. This usually means the "
            "Habitica API changed; please report it at "
            "https://github.com/CDMonsalveA/habitica-mcp/issues."
        )


class ToolInputError(HabiticaMCPError):
    """The arguments make sense to the schema but not to Habitica's rules."""

    log_level_default: ClassVar[int] = logging.INFO


class TaskNotFoundError(HabiticaMCPError):
    """No task matches a reference."""

    log_level_default: ClassVar[int] = logging.INFO

    def __init__(self, reference: str, suggestions: Sequence[str] = ()) -> None:
        message = f"No task matches {reference!r}."
        if suggestions:
            message += " Did you mean: " + "; ".join(suggestions) + "?"
        message += " Use habitica_list_tasks to find the right task id."
        super().__init__(message)


class AmbiguousReferenceError(HabiticaMCPError):
    """A reference matches several items; the model must pick one."""

    log_level_default: ClassVar[int] = logging.INFO

    def __init__(self, kind: str, reference: str, candidates: Sequence[str]) -> None:
        listing = "; ".join(candidates)
        super().__init__(
            f"{reference!r} matches several {kind}s: {listing}. "
            "Call the tool again with the exact id of the one the user means."
        )
