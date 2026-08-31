"""Phase 2: the 31-day retention sweep (PRD 9, 10)."""

import csv
import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from app.models import Article, Source, SourceType
from app.storage import Dataset

IST = ZoneInfo("Asia/Kolkata")
SEP_1 = dt.datetime(2026, 9, 1, 7, 30, tzinfo=IST)


@pytest.mark.unit
def test_cutoff_matches_the_prd_example(repo):
    """PRD 9: September 1 minus 31 days deletes everything before August 1."""
    assert repo.cutoff_date(SEP_1) == dt.date(2026, 8, 1)


@pytest.mark.unit
def test_row_inside_the_window_survives(repo):
    from tests.conftest import make_article

    repo.write(Dataset.ARTICLES, [make_article("2026-08-02T09:00:00+05:30")])

    repo.cleanup_old_data(now=SEP_1)

    assert len(repo.read(Dataset.ARTICLES)) == 1


@pytest.mark.unit
def test_row_outside_the_window_is_deleted(repo):
    from tests.conftest import make_article

    repo.write(Dataset.ARTICLES, [make_article("2026-07-30T09:00:00+05:30")])

    report = repo.cleanup_old_data(now=SEP_1)

    assert repo.read(Dataset.ARTICLES) == []
    assert report.removed[Dataset.ARTICLES] == 1


@pytest.mark.unit
def test_cutoff_day_itself_is_kept(repo):
    """The boundary is inclusive: >= cutoff survives."""
    from tests.conftest import make_article

    repo.write(Dataset.ARTICLES, [make_article("2026-08-01T00:00:00+05:30")])

    repo.cleanup_old_data(now=SEP_1)

    assert len(repo.read(Dataset.ARTICLES)) == 1


@pytest.mark.unit
def test_retention_boundary_is_evaluated_in_ist_not_utc(repo):
    """A row stamped 00:30 IST on the cutoff day is still July 31 in UTC.

    Judging it by the UTC date would silently delete a row that is inside the window.
    This is the single most likely timezone bug in the retention path.
    """
    from tests.conftest import make_article

    just_after_midnight_ist = "2026-08-01T00:30:00+05:30"
    assert dt.datetime.fromisoformat(just_after_midnight_ist).astimezone(
        ZoneInfo("UTC")
    ).date() == dt.date(2026, 7, 31), "premise: this instant is the previous day in UTC"

    repo.write(Dataset.ARTICLES, [make_article(just_after_midnight_ist)])
    repo.cleanup_old_data(now=SEP_1)

    assert len(repo.read(Dataset.ARTICLES)) == 1


@pytest.mark.unit
def test_mixed_ages_keep_only_the_recent(repo):
    from tests.conftest import make_article

    repo.write(
        Dataset.ARTICLES,
        [
            make_article("2026-08-30T09:00:00+05:30", article_id="fresh"),
            make_article("2026-08-01T09:00:00+05:30", article_id="boundary"),
            make_article("2026-07-31T09:00:00+05:30", article_id="stale"),
            make_article("2026-06-01T09:00:00+05:30", article_id="ancient"),
        ],
    )

    report = repo.cleanup_old_data(now=SEP_1)

    kept = {a.id for a in repo.read(Dataset.ARTICLES)}
    assert kept == {"fresh", "boundary"}
    assert report.removed[Dataset.ARTICLES] == 2
    assert report.total_removed == 2


@pytest.mark.unit
def test_cleanup_deletes_rows_not_files(repo, data_dir):
    """PRD 10 is explicit: do not delete the CSV files themselves."""
    from tests.conftest import make_article

    repo.write(Dataset.ARTICLES, [make_article("2026-01-01T09:00:00+05:30")])
    path = data_dir / "articles.csv"

    repo.cleanup_old_data(now=SEP_1)

    assert path.exists()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(Article.model_fields), "header must survive"
        assert list(reader) == []


@pytest.mark.unit
def test_sources_registry_is_exempt_from_retention(repo):
    """sources.csv holds accumulated reliability evidence, not time-series rows.

    Pruning it would reset every domain's reputation monthly and flatten the
    source-quality weighting that Phase 5 ranking depends on.
    """
    repo.write(
        Dataset.SOURCES,
        [
            Source(
                source_domain="reuters.com",
                source_name="Reuters",
                source_type=SourceType.NEWS,
                reliability_score=0.95,
                last_success=dt.datetime(2025, 1, 1, tzinfo=IST),
            )
        ],
    )

    report = repo.cleanup_old_data(now=SEP_1)

    assert len(repo.read(Dataset.SOURCES)) == 1
    assert Dataset.SOURCES not in report.removed


@pytest.mark.unit
def test_unparseable_timestamp_is_kept_not_silently_dropped(repo, data_dir):
    """Deleting data we cannot date is worse than carrying it."""
    data_dir.mkdir(parents=True, exist_ok=True)
    fields = list(Article.model_fields)
    with (data_dir / "articles.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(dict.fromkeys(fields, "") | {
            "id": "x", "title": "t", "url": "https://e.com", "source": "E",
            "source_domain": "e.com", "collected_at": "not-a-timestamp",
            "content_hash": "h", "language": "en", "category": "",
        })

    report = repo.cleanup_old_data(now=SEP_1)

    assert Dataset.ARTICLES not in report.removed


@pytest.mark.unit
def test_cleanup_on_empty_store_is_a_no_op(repo):
    report = repo.cleanup_old_data(now=SEP_1)

    assert report.total_removed == 0


@pytest.mark.unit
@freeze_time("2026-09-01T02:00:00Z")
def test_default_now_uses_the_briefing_timezone(repo):
    """02:00 UTC is 07:30 IST on Sep 1, so the cutoff is Aug 1."""
    assert repo.cutoff_date() == dt.date(2026, 8, 1)


@pytest.mark.unit
def test_topics_are_pruned_by_last_seen(repo):
    from app.models import Section, Topic

    old = dt.datetime(2026, 6, 1, tzinfo=IST)
    repo.write(
        Dataset.TOPICS,
        [
            Topic(
                topic_id="t1", headline="Old story", section=Section.GLOBAL,
                first_seen=old, last_seen=old, created_at=old,
            )
        ],
    )

    repo.cleanup_old_data(now=SEP_1)

    assert repo.read(Dataset.TOPICS) == []
