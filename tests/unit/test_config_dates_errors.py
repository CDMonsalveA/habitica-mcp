from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from habitica_mcp.config import Settings
from habitica_mcp.const import MAINTAINER_USER_ID
from habitica_mcp.dates import UserClock
from habitica_mcp.errors import HabiticaAPIError, MissingCredentialsError

USER_ID = "00000000-0000-4000-8000-000000000001"
TOKEN = "00000000-0000-4000-8000-0000000000aa"


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HABITICA_USER_ID", USER_ID)
    monkeypatch.setenv("HABITICA_API_TOKEN", f"  {TOKEN} ")
    monkeypatch.setenv("HABITICA_MCP_MODE", "READ_ONLY")
    monkeypatch.setenv("HABITICA_MCP_LOG_LEVEL", "info")
    monkeypatch.setenv("HABITICA_BASE_URL", "http://localhost:3000/")
    monkeypatch.setenv("HABITICA_X_CLIENT", "")

    settings = Settings()

    assert settings.credentials() == (USER_ID, TOKEN)
    assert settings.read_only
    assert settings.log_level == "INFO"
    assert settings.base_url == "http://localhost:3000"
    assert settings.x_client is None
    assert "aa" not in repr(settings), "the token must never be printed"


def test_x_client_identifies_the_maintainer_unless_overridden() -> None:
    settings = Settings.model_validate({"user_id": USER_ID, "api_token": TOKEN})
    assert settings.resolved_x_client() == f"{MAINTAINER_USER_ID}-habitica-mcp"

    custom = Settings.model_validate(
        {"user_id": USER_ID, "api_token": TOKEN, "x_client": f"{USER_ID}-my-fork"}
    )
    assert custom.resolved_x_client() == f"{USER_ID}-my-fork"


def test_missing_credentials_explain_how_to_configure() -> None:
    settings = Settings()
    with pytest.raises(MissingCredentialsError, match="HABITICA_USER_ID and HABITICA_API_TOKEN"):
        settings.credentials()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("api_token", "not-a-token", "must look like a UUID"),
        ("x_client", "my-tool", "must start with a Habitica User ID"),
        ("base_url", "habitica.com", "must start with http"),
        ("now", datetime(2026, 9, 16, 9, 0), "must include a UTC offset"),
        ("rate_limit_per_minute", 31, "less than or equal to 30"),
    ],
)
def test_invalid_settings_are_rejected(field: str, value: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings.model_validate({field: value})


def test_user_clock_respects_time_zone_and_custom_day_start() -> None:
    # 2026-09-16 03:30 UTC is 22:30 on the 15th in UTC-5.
    clock = UserClock(datetime(2026, 9, 16, 3, 30, tzinfo=UTC), timezone_offset_minutes=300)
    assert clock.today() == date(2026, 9, 15)
    assert clock.local_date(datetime(2026, 9, 19, 5, 0, tzinfo=UTC)) == date(2026, 9, 19)
    assert clock.local_midnight(date(2026, 9, 19)) == datetime(2026, 9, 19, 5, 0, tzinfo=UTC)

    # With the day starting at 4:00, 02:00 local still belongs to the previous day.
    early = UserClock(datetime(2026, 9, 16, 7, 0, tzinfo=UTC), 300, day_start_hour=4)
    assert early.today() == date(2026, 9, 15)


def test_api_errors_distinguish_bad_credentials_from_business_rules() -> None:
    bad_login = HabiticaAPIError.from_payload(
        401,
        {"error": "NotAuthorized", "message": "There is no account that uses those credentials."},
    )
    no_gold = HabiticaAPIError.from_payload(
        401, {"error": "NotAuthorized", "message": "Not Enough Gold"}
    )
    validation = HabiticaAPIError.from_payload(
        400,
        {
            "error": "BadRequest",
            "message": "Invalid request parameters.",
            "errors": [{"message": "type is wrong"}],
        },
    )

    assert "Check HABITICA_USER_ID" in str(bad_login)
    assert str(no_gold) == "Habitica refused the action: Not Enough Gold"
    assert "type is wrong" in str(validation)
    assert "internal problem" in str(HabiticaAPIError.from_payload(503, None))
    assert "could not find it" in str(
        HabiticaAPIError.from_payload(404, {"message": "Task not found."})
    )
    assert "does not allow" in str(HabiticaAPIError.from_payload(403, {"message": "Blocked"}))
