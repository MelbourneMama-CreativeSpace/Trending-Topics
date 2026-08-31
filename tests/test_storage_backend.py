"""Phase 2: reading, round-tripping, and corruption recovery (PRD 70)."""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.models import Article
from app.storage import Dataset

IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.unit
def test_missing_file_reads_as_empty(repo):
    """A first-ever run has no files. That is not an error."""
    assert repo.read(Dataset.ARTICLES) == []


@pytest.mark.unit
def test_zero_byte_file_reads_as_empty(backend, repo, data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "articles.csv").write_text("", encoding="utf-8")

    assert repo.read(Dataset.ARTICLES) == []


@pytest.mark.unit
def test_round_trip_preserves_timezone_aware_timestamps(repo):
    """A naive timestamp coming back out would corrupt every retention decision."""
    from tests.conftest import make_article

    original = make_article("2026-09-01T07:30:00+05:30")
    repo.write(Dataset.ARTICLES, [original])

    restored = repo.read(Dataset.ARTICLES)[0]

    assert restored.collected_at.tzinfo is not None
    assert restored.collected_at == original.collected_at
    assert restored.collected_at.utcoffset() == dt.timedelta(hours=5, minutes=30)


@pytest.mark.unit
def test_round_trip_distinguishes_empty_string_from_none(repo):
    """`category` defaults to "" and `topic_id` is None -- both write an empty cell.

    They must come back as they went in, or validation fails on the next read.
    """
    from tests.conftest import make_article

    repo.write(Dataset.ARTICLES, [make_article("2026-09-01T07:30:00+05:30")])

    restored = repo.read(Dataset.ARTICLES)[0]

    assert restored.category == ""
    assert restored.topic_id is None
    assert restored.published_at is None


@pytest.mark.unit
def test_append_preserves_existing_rows(repo):
    from tests.conftest import make_article

    repo.write(Dataset.ARTICLES, [make_article("2026-09-01T07:00:00+05:30", "a1")])
    repo.append(Dataset.ARTICLES, [make_article("2026-09-01T08:00:00+05:30", "a2")])

    assert {a.id for a in repo.read(Dataset.ARTICLES)} == {"a1", "a2"}


@pytest.mark.unit
def test_corrupt_header_is_quarantined_and_the_run_continues(repo, data_dir, caplog):
    """PRD 70: preserve the file, make a recovery copy, keep going."""
    data_dir.mkdir(parents=True, exist_ok=True)
    broken = data_dir / "articles.csv"
    broken.write_text("these,are,not,the,right,columns\n1,2,3,4,5,6\n", encoding="utf-8")

    result = repo.read(Dataset.ARTICLES)

    assert result == [], "must degrade to empty, not raise"
    assert broken.exists(), "original must be preserved"
    copies = list(data_dir.glob("articles.corrupt-*.csv"))
    assert len(copies) == 1, "a recovery copy must be written"
    assert "not,the,right,columns" in copies[0].read_text(encoding="utf-8")


@pytest.mark.unit
def test_undecodable_bytes_are_quarantined(repo, data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "articles.csv").write_bytes(b"\xff\xfe\x00invalid utf-8 \xc3\x28")

    assert repo.read(Dataset.ARTICLES) == []
    assert list(data_dir.glob("articles.corrupt-*.csv"))


@pytest.mark.unit
def test_corruption_is_logged_with_the_prd_error_code(repo, data_dir, run_log):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "articles.csv").write_text("wrong,header\n1,2\n", encoding="utf-8")

    repo.read(Dataset.ARTICLES)

    assert any("CSV_READ_FAILED" in message for message in run_log.messages)


@pytest.mark.unit
def test_a_single_bad_row_does_not_cost_the_whole_file(repo, data_dir, run_log):
    """One record from an older schema must not discard a month of history."""
    import csv

    data_dir.mkdir(parents=True, exist_ok=True)
    fields = list(Article.model_fields)
    good = {
        "id": "good", "title": "Fine", "url": "https://e.com", "source": "E",
        "source_domain": "e.com", "published_at": "", "collected_at": "2026-09-01T07:30:00+05:30",
        "language": "en", "category": "", "content_hash": "h", "topic_id": "",
    }
    bad = {**good, "id": "bad", "collected_at": "definitely-not-a-datetime"}

    with (data_dir / "articles.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(good)
        writer.writerow(bad)

    records = repo.read(Dataset.ARTICLES)

    assert [a.id for a in records] == ["good"]
    assert any("CSV_ROW_SKIPPED" in message for message in run_log.messages)


@pytest.mark.unit
def test_write_then_read_is_stable_across_repeated_cycles(repo):
    """Guards against a serialisation asymmetry that drifts a little each run."""
    from tests.conftest import make_article

    records = [make_article("2026-09-01T07:30:00+05:30")]
    for _ in range(3):
        repo.write(Dataset.ARTICLES, records)
        records = repo.read(Dataset.ARTICLES)

    assert len(records) == 1
    assert records[0].collected_at == dt.datetime(2026, 9, 1, 7, 30, tzinfo=IST)
