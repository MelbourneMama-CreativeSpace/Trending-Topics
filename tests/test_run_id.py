"""Phase 1: run identity (PRD 74)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from app.run_context import generate_run_id, is_valid_run_id

IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.unit
def test_run_id_matches_prd_format():
    """PRD 74 gives 20260901-073000-a81f as the shape."""
    assert is_valid_run_id(generate_run_id(IST))


@pytest.mark.unit
@freeze_time("2026-09-01T02:00:00Z")
def test_run_id_timestamp_is_rendered_in_ist_not_utc():
    """02:00 UTC is 07:30 IST -- the scheduled send time.

    If this ever renders 020000, run_ids are being stamped in UTC and every log line
    will disagree with the day the operator is looking at.
    """
    assert generate_run_id(IST).startswith("20260901-073000-")


@pytest.mark.unit
@freeze_time("2026-08-31T19:00:00Z")
def test_run_id_uses_the_local_date_across_the_utc_day_boundary():
    """19:00 UTC on Aug 31 is already 00:30 IST on Sep 1."""
    assert generate_run_id(IST).startswith("20260901-003000-")


@pytest.mark.unit
def test_run_ids_are_unique_within_the_same_second():
    """The random suffix is what keeps two runs in one second distinguishable."""
    fixed = datetime(2026, 9, 1, 7, 30, 0, tzinfo=IST)

    generated = {generate_run_id(IST, now=fixed) for _ in range(500)}

    assert len(generated) > 400


@pytest.mark.unit
def test_naive_free_explicit_now_is_converted_to_the_briefing_zone():
    utc_moment = datetime(2026, 9, 1, 2, 0, tzinfo=ZoneInfo("UTC"))

    assert generate_run_id(IST, now=utc_moment).startswith("20260901-073000-")


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        "",
        "20260901-073000",
        "20260901-073000-A81F",
        "20260901-073000-a81",
        "2026091-073000-a81f",
        "not-a-run-id",
    ],
)
def test_invalid_run_ids_are_rejected(value):
    assert not is_valid_run_id(value)
