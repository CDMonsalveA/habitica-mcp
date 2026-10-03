"""Whether Habitica still has to start the user's new day ("cron").

Habitica computes ``needsCron`` only for the whole user document, which is large. Its rule
(``User.daysUserHasMissed``) compares the Habitica day of ``lastCron`` with today's, in the
user's time zone and custom day start. That rule is reproduced here from small projected
fields; when the time zone changed since the last cron Habitica applies extra rules, so
the whole document is fetched instead.
"""

from __future__ import annotations

from habiticalib import UserData

from .dates import UserClock
from .gateway import HabiticaGateway


def estimate_needs_cron(user: UserData, clock: UserClock) -> bool | None:
    """``True``/``False`` like Habitica, or ``None`` when only Habitica can tell."""
    if user.lastCron is None:
        return None
    offset_at_last_cron = user.preferences.timezoneOffsetAtLastCron
    if offset_at_last_cron is not None and offset_at_last_cron != clock.timezone_offset_minutes:
        return None
    return clock.today() > clock.habitica_day(user.lastCron)


async def needs_cron(gateway: HabiticaGateway, user: UserData, clock: UserClock) -> bool:
    estimate = estimate_needs_cron(user, clock)
    if estimate is not None:
        return estimate
    return bool((await gateway.get_full_user()).needsCron)
