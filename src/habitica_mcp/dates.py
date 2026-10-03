"""Calendar maths in the user's Habitica time zone.

Habitica stores a JavaScript-style ``timezoneOffset`` (minutes *behind* UTC, so UTC-5
is ``300``) and a custom day start hour. "Today" for dailies starts at that hour; to-do
due dates are plain calendar dates shown in the user's local time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta


@dataclass(frozen=True, slots=True)
class UserClock:
    """Converts between UTC instants and the user's local Habitica calendar."""

    now_utc: datetime
    timezone_offset_minutes: int = 0
    day_start_hour: int = 0

    @property
    def _offset(self) -> timedelta:
        return timedelta(minutes=self.timezone_offset_minutes)

    def local(self, instant: datetime) -> datetime:
        """Naive local wall-clock time for an aware instant."""
        return instant.astimezone(UTC).replace(tzinfo=None) - self._offset

    def habitica_day(self, instant: datetime) -> date:
        """The Habitica day an instant belongs to (days begin at the custom day start)."""
        return (self.local(instant) - timedelta(hours=self.day_start_hour)).date()

    def today(self) -> date:
        """The user's current Habitica day."""
        return self.habitica_day(self.now_utc)

    def local_date(self, instant: datetime) -> date:
        """Calendar date of an instant in the user's time zone (for to-do due dates)."""
        return self.local(instant).date()

    def local_midnight(self, day: date) -> datetime:
        """The UTC instant of local midnight, which is how Habitica's apps store dates."""
        return datetime.combine(day, time(), tzinfo=UTC) + self._offset
