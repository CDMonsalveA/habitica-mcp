"""needsCron is derived like Habitica's User.daysUserHasMissed (no time zone change)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from habiticalib import UserData

from habitica_mcp.cron import estimate_needs_cron
from habitica_mcp.dates import UserClock

# 09:00 on Wednesday 2026-09-16 in UTC-5.
NOW = datetime(2026, 9, 16, 14, 0, tzinfo=UTC)


def user(last_cron: str | None, **preferences: Any) -> UserData:
    data: dict[str, Any] = {"preferences": {"timezoneOffset": 300, **preferences}}
    if last_cron is not None:
        data["lastCron"] = last_cron
    return UserData.from_dict(data)


def test_cron_is_pending_when_the_last_cron_was_on_a_previous_habitica_day() -> None:
    clock = UserClock(NOW, timezone_offset_minutes=300)

    assert estimate_needs_cron(user("2026-09-16T12:00:00Z"), clock) is False  # 07:00 today
    assert estimate_needs_cron(user("2026-09-16T04:59:00Z"), clock) is True  # 23:59 yesterday
    assert estimate_needs_cron(user("2026-09-10T12:00:00Z"), clock) is True


def test_custom_day_start_moves_the_boundary() -> None:
    # Day starts at 10:00: at 09:00 it is still "yesterday" for Habitica.
    clock = UserClock(NOW, timezone_offset_minutes=300, day_start_hour=10)

    assert estimate_needs_cron(user("2026-09-15T16:00:00Z"), clock) is False  # 11:00 yesterday
    assert estimate_needs_cron(user("2026-09-15T13:00:00Z"), clock) is True  # 08:00 yesterday


def test_unknown_when_the_time_zone_changed_or_cron_never_ran() -> None:
    clock = UserClock(NOW, timezone_offset_minutes=300)

    assert (
        estimate_needs_cron(user("2026-09-15T12:00:00Z", timezoneOffsetAtLastCron=240), clock)
        is None
    )
    assert estimate_needs_cron(user(None), clock) is None
    assert (
        estimate_needs_cron(user("2026-09-15T12:00:00Z", timezoneOffsetAtLastCron=300), clock)
        is True
    )
