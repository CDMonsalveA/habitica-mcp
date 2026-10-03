"""Runtime configuration read from environment variables.

Credentials are optional at startup on purpose: MCP clients and directories list the
tools of a server before any credentials exist. Tools that need Habitica raise
:class:`~habitica_mcp.errors.MissingCredentialsError` with setup instructions instead.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .const import APP_NAME, DEFAULT_BASE_URL, HABITICA_RATE_LIMIT_PER_MINUTE, MAINTAINER_USER_ID
from .errors import MissingCredentialsError

Mode = Literal["standard", "read-only"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Server settings. Field docs double as the configuration reference."""

    model_config = SettingsConfigDict(
        extra="ignore",
        populate_by_name=True,
        env_ignore_empty=True,
        frozen=True,
    )

    user_id: UUID | None = Field(
        default=None,
        validation_alias="HABITICA_USER_ID",
        description="Habitica User ID (Settings > Site Data).",
    )
    api_token: SecretStr | None = Field(
        default=None,
        validation_alias="HABITICA_API_TOKEN",
        description="Habitica API Token. Never logged.",
    )
    x_client: str | None = Field(
        default=None,
        validation_alias="HABITICA_X_CLIENT",
        description="Override for the x-client header ('<author user id>-<tool name>').",
    )
    base_url: str = Field(
        default=DEFAULT_BASE_URL,
        validation_alias="HABITICA_BASE_URL",
        description="Habitica server, e.g. a self-hosted instance or a test double.",
    )
    mode: Mode = Field(
        default="standard",
        validation_alias="HABITICA_MCP_MODE",
        description="'read-only' hides every tool that changes Habitica.",
    )
    rate_limit_per_minute: int = Field(
        default=25,
        ge=1,
        le=HABITICA_RATE_LIMIT_PER_MINUTE,
        validation_alias="HABITICA_MCP_RATE_LIMIT",
        description="Client-side cap on Habitica requests per minute (Habitica allows 30).",
    )
    max_rate_limit_wait_seconds: float = Field(
        default=10.0,
        ge=0,
        le=60,
        validation_alias="HABITICA_MCP_MAX_WAIT",
        description="Longest wait for a free request slot before failing fast.",
    )
    cache_ttl_seconds: float = Field(
        default=15.0,
        ge=0,
        le=300,
        validation_alias="HABITICA_MCP_CACHE_TTL",
        description="How long reads are reused between tool calls (0 disables).",
    )
    request_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        le=120,
        validation_alias="HABITICA_MCP_TIMEOUT",
    )
    log_level: LogLevel = Field(default="WARNING", validation_alias="HABITICA_MCP_LOG_LEVEL")
    metrics_file: Path | None = Field(
        default=None,
        validation_alias="HABITICA_MCP_METRICS_FILE",
        description="Append one JSON line per tool call to this file.",
    )
    now: datetime | None = Field(
        default=None,
        validation_alias="HABITICA_MCP_NOW",
        description="Testing and evals only: freeze the current time (ISO 8601 with offset).",
    )

    @field_validator("api_token", mode="before")
    @classmethod
    def _strip_token(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("api_token")
    @classmethod
    def _token_is_uuid(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                UUID(value.get_secret_value())
            except ValueError:
                raise ValueError(
                    "HABITICA_API_TOKEN must look like a UUID (copy it from Habitica > "
                    "Settings > Site Data)"
                ) from None
        return value

    @field_validator("x_client")
    @classmethod
    def _x_client_starts_with_user_id(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            try:
                UUID(value[:36])
            except ValueError:
                raise ValueError(
                    "HABITICA_X_CLIENT must start with a Habitica User ID, e.g. '<user id>-my-tool'"
                ) from None
        return value

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("HABITICA_BASE_URL must start with http:// or https://")
        return value

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower().replace("_", "-").replace("readonly", "read-only")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("now")
    @classmethod
    def _now_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError(
                "HABITICA_MCP_NOW must include a UTC offset, e.g. 2026-09-16T09:00:00-05:00"
            )
        return value

    @property
    def read_only(self) -> bool:
        return self.mode == "read-only"

    def credentials(self) -> tuple[str, str]:
        """Return ``(user_id, api_token)`` or explain how to configure them."""
        if self.user_id is None or self.api_token is None:
            raise MissingCredentialsError()
        return str(self.user_id), self.api_token.get_secret_value()

    def resolved_x_client(self) -> str:
        """The x-client header value Habitica requires on every request."""
        if self.x_client:
            return self.x_client
        owner = MAINTAINER_USER_ID or (str(self.user_id) if self.user_id else None)
        if owner is None:
            raise MissingCredentialsError()
        return f"{owner}-{APP_NAME}"
