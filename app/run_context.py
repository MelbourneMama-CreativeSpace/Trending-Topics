"""Run identity (PRD 74).

`run_id` is generated in the briefing timezone, not UTC, so a log line's timestamp
matches the day the operator is actually looking at.
"""

import re
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

# e.g. 20260901-073000-a81f
RUN_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")

_RANDOM_SUFFIX_BYTES = 2


def generate_run_id(tz: ZoneInfo, now: datetime | None = None) -> str:
    """Build a `run_id` like `20260901-073000-a81f`.

    The random suffix keeps two runs started in the same second distinguishable;
    it is not a security value.
    """
    moment = now.astimezone(tz) if now is not None else datetime.now(tz)
    return f"{moment:%Y%m%d-%H%M%S}-{secrets.token_hex(_RANDOM_SUFFIX_BYTES)}"


def is_valid_run_id(value: str) -> bool:
    return bool(RUN_ID_PATTERN.match(value))
