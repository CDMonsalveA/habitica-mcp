"""Project-wide constants."""

from typing import Final

APP_NAME: Final = "habitica-mcp"
REPOSITORY_URL: Final = "https://github.com/CDMonsalveA/habitica-mcp"
DEFAULT_BASE_URL: Final = "https://habitica.com"

# Habitica asks every third-party tool to send an ``x-client`` header made of the tool
# author's Habitica User ID and the tool name, so its team can contact the author:
# https://github.com/HabitRPG/habitica/wiki/API-Usage-Guidelines
# User IDs are public (they appear in profiles); forks should set HABITICA_X_CLIENT.
MAINTAINER_USER_ID: Final[str | None] = "53c732c9-2e10-4438-97c4-c726f604f8a7"

# Habitica's server allows 30 requests per minute per user (middlewares/rateLimiter.js).
HABITICA_RATE_LIMIT_PER_MINUTE: Final = 30
RATE_LIMIT_PERIOD_SECONDS: Final = 60.0

DEFAULT_LIST_LIMIT: Final = 20
MAX_LIST_LIMIT: Final = 100
OVERVIEW_LIST_CAP: Final = 25
OVERVIEW_HABITS_CAP: Final = 15
UPCOMING_DAYS: Final = 7
CONCISE_NOTES_CHARS: Final = 160
MAX_SUGGESTIONS: Final = 5
LOW_HP_THRESHOLD: Final = 15.0
# common/script/constants.js
MAX_HEALTH: Final = 50

# User preferences (time zone, custom day start) change rarely; keep them longer than tasks.
PREFERENCES_TTL_SECONDS: Final = 600.0
