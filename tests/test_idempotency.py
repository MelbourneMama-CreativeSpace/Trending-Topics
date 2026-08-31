"""Phase 2: duplicate-briefing protection (PRD 48, 73).

The rule that matters: a briefing already *delivered* must never be sent twice, and a
briefing that failed to deliver must remain retryable.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.models import BriefingStatus, EmailStatus, build_briefing_id
from app.storage import Dataset

IST = ZoneInfo("Asia/Kolkata")
TODAY = dt.date(2026, 9, 1)
TODAY_ID = build_briefing_id(TODAY, "Asia/Kolkata")


@pytest.mark.unit
def test_briefing_id_is_date_plus_timezone():
    assert TODAY_ID == "2026-09-01-Asia/Kolkata"


@pytest.mark.unit
def test_no_record_means_not_yet_delivered(repo):
    assert repo.was_already_delivered(TODAY_ID) is False


@pytest.mark.unit
def test_completed_and_sent_blocks_a_second_send(repo):
    from tests.conftest import make_briefing

    repo.upsert_briefing(
        make_briefing(TODAY, status=BriefingStatus.COMPLETED, email_status=EmailStatus.SENT)
    )

    assert repo.was_already_delivered(TODAY_ID) is True


@pytest.mark.unit
def test_partial_and_sent_also_blocks_a_second_send(repo):
    """PRD 62: four verified niche stories is a valid send, so it counts as delivered."""
    from tests.conftest import make_briefing

    repo.upsert_briefing(
        make_briefing(TODAY, status=BriefingStatus.PARTIAL, email_status=EmailStatus.SENT)
    )

    assert repo.was_already_delivered(TODAY_ID) is True


@pytest.mark.unit
def test_started_but_unfinished_does_not_block(repo):
    """A crashed run must not lock the day out permanently."""
    from tests.conftest import make_briefing

    repo.upsert_briefing(make_briefing(TODAY, status=BriefingStatus.STARTED))

    assert repo.was_already_delivered(TODAY_ID) is False


@pytest.mark.unit
def test_completed_but_email_failed_is_still_retryable(repo):
    """The email is the deliverable. If it never sent, the day is not done.

    Treating status alone as the signal would silently skip the retry.
    """
    from tests.conftest import make_briefing

    repo.upsert_briefing(
        make_briefing(TODAY, status=BriefingStatus.COMPLETED, email_status=EmailStatus.FAILED)
    )

    assert repo.was_already_delivered(TODAY_ID) is False


@pytest.mark.unit
def test_dry_run_does_not_count_as_delivered(repo):
    """A dry run renders but deliberately does not send (PRD 86)."""
    from tests.conftest import make_briefing

    repo.upsert_briefing(
        make_briefing(TODAY, status=BriefingStatus.COMPLETED, email_status=EmailStatus.SKIPPED)
    )

    assert repo.was_already_delivered(TODAY_ID) is False


@pytest.mark.unit
def test_failed_run_does_not_block(repo):
    from tests.conftest import make_briefing

    repo.upsert_briefing(make_briefing(TODAY, status=BriefingStatus.FAILED))

    assert repo.was_already_delivered(TODAY_ID) is False


@pytest.mark.unit
def test_yesterdays_delivery_does_not_block_today(repo):
    from tests.conftest import make_briefing

    repo.upsert_briefing(
        make_briefing(
            dt.date(2026, 8, 31),
            status=BriefingStatus.COMPLETED,
            email_status=EmailStatus.SENT,
        )
    )

    assert repo.was_already_delivered(TODAY_ID) is False


@pytest.mark.unit
def test_upsert_replaces_rather_than_duplicating(repo):
    """A run updates its own record as it progresses: started then completed."""
    from tests.conftest import make_briefing

    repo.upsert_briefing(make_briefing(TODAY, status=BriefingStatus.STARTED))
    repo.upsert_briefing(
        make_briefing(
            TODAY,
            status=BriefingStatus.COMPLETED,
            email_status=EmailStatus.SENT,
            completed_at=dt.datetime(2026, 9, 1, 7, 33, tzinfo=IST),
            global_count=5,
            niche_count=5,
        )
    )

    records = repo.read(Dataset.BRIEFINGS)
    assert len(records) == 1
    assert records[0].status == BriefingStatus.COMPLETED
    assert records[0].global_count == 5


@pytest.mark.unit
def test_upsert_leaves_other_days_untouched(repo):
    from tests.conftest import make_briefing

    repo.upsert_briefing(make_briefing(dt.date(2026, 8, 31)))
    repo.upsert_briefing(make_briefing(TODAY))
    repo.upsert_briefing(make_briefing(TODAY, status=BriefingStatus.COMPLETED))

    assert len(repo.read(Dataset.BRIEFINGS)) == 2


@pytest.mark.unit
def test_find_briefing_returns_none_for_an_unknown_id(repo):
    assert repo.find_briefing("2099-01-01-Asia/Kolkata") is None


@pytest.mark.unit
def test_a_zone_change_produces_a_different_key():
    """Including the timezone stops a zone change colliding with an existing record."""
    assert build_briefing_id(TODAY, "Australia/Melbourne") != TODAY_ID
