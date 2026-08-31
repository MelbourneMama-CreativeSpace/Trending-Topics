"""Phase 0 exit criteria: the environment itself is sound.

These guard the two things that silently break this project on a fresh machine or a
slim container: a missing timezone database, and a runtime older than the PRD requires.
"""

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

BRIEFING_TZ = "Asia/Kolkata"


@pytest.mark.unit
def test_python_version_meets_prd_minimum():
    assert sys.version_info >= (3, 12), "PRD requires Python 3.12+"


@pytest.mark.unit
def test_briefing_timezone_resolves():
    """Fails without `tzdata` installed — Windows and slim Linux images ship no tz database.

    Every retention cutoff and briefing_id depends on this zone.
    """
    now = datetime.now(ZoneInfo(BRIEFING_TZ))
    assert now.tzinfo is not None
    assert now.utcoffset() is not None


@pytest.mark.unit
def test_ist_offset_is_fixed_year_round():
    """IST is UTC+5:30 with no daylight saving, in either hemisphere's summer.

    This is what lets the GitHub Actions cron stay a constant UTC time all year.
    If this ever fails, the 7:30 AM schedule has drifted.
    """
    ist = ZoneInfo(BRIEFING_TZ)
    winter = datetime(2026, 1, 15, 12, 0, tzinfo=ist)
    summer = datetime(2026, 7, 15, 12, 0, tzinfo=ist)

    expected = 5.5 * 3600
    assert winter.utcoffset().total_seconds() == expected
    assert summer.utcoffset().total_seconds() == expected


@pytest.mark.unit
def test_scheduled_send_time_maps_to_expected_utc():
    """07:30 IST must be 02:00 UTC — the value the GitHub Actions cron will use."""
    ist = ZoneInfo(BRIEFING_TZ)
    send_time = datetime(2026, 9, 1, 7, 30, tzinfo=ist)
    utc = send_time.astimezone(ZoneInfo("UTC"))

    assert (utc.hour, utc.minute) == (2, 0)


@pytest.mark.unit
def test_runtime_dependencies_import():
    """Every runtime import the pipeline will need, checked in one place."""
    import feedparser  # noqa: F401
    import httpx  # noqa: F401
    import jinja2  # noqa: F401
    import pydantic  # noqa: F401
    import pydantic_settings  # noqa: F401
    import rapidfuzz  # noqa: F401
    import tenacity  # noqa: F401
    from fastapi import FastAPI  # noqa: F401


@pytest.mark.unit
def test_app_package_importable():
    import app

    assert app.__version__
