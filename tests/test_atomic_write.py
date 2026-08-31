"""Phase 2: crash-safe CSV writes (PRD 11)."""

import csv

import pytest

from app.errors import BriefingError, ErrorCode
from app.storage import Dataset, temp_path_for
from app.storage.atomic import atomic_write_csv

FIELDS = ["id", "title"]


def _read(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.unit
def test_writes_rows_and_header(tmp_path):
    target = tmp_path / "topics.csv"

    written = atomic_write_csv(target, FIELDS, [{"id": "1", "title": "One"}])

    assert written == 1
    assert _read(target) == [{"id": "1", "title": "One"}]


@pytest.mark.unit
def test_creates_missing_parent_directory(tmp_path):
    target = tmp_path / "nested" / "data" / "topics.csv"

    atomic_write_csv(target, FIELDS, [])

    assert target.exists()


@pytest.mark.unit
def test_temp_file_is_named_per_prd(tmp_path):
    """PRD 11 pairs topics.csv with topics.tmp.csv."""
    assert temp_path_for(tmp_path / "topics.csv").name == "topics.tmp.csv"


@pytest.mark.unit
def test_crash_midway_leaves_the_original_intact(tmp_path):
    """The whole point of PRD 11: a failed write must not destroy existing data."""
    target = tmp_path / "topics.csv"
    atomic_write_csv(target, FIELDS, [{"id": "1", "title": "Original"}])

    def exploding_rows():
        yield {"id": "2", "title": "New"}
        raise RuntimeError("serialisation blew up mid-stream")

    with pytest.raises(BriefingError) as caught:
        atomic_write_csv(target, FIELDS, exploding_rows())

    assert caught.value.code == ErrorCode.CSV_WRITE_FAILED
    assert _read(target) == [{"id": "1", "title": "Original"}], "original was clobbered"


@pytest.mark.unit
def test_crash_midway_removes_the_temp_file(tmp_path):
    """A stranded .tmp.csv would be committed to the repo by the Phase 9 backend."""
    target = tmp_path / "topics.csv"

    def exploding_rows():
        yield {"id": "1", "title": "One"}
        raise RuntimeError("boom")

    with pytest.raises(BriefingError):
        atomic_write_csv(target, FIELDS, exploding_rows())

    assert not temp_path_for(target).exists()


@pytest.mark.unit
def test_unknown_column_is_rejected_before_replacing(tmp_path):
    target = tmp_path / "topics.csv"
    atomic_write_csv(target, FIELDS, [{"id": "1", "title": "Original"}])

    with pytest.raises(BriefingError):
        atomic_write_csv(target, FIELDS, [{"id": "2", "title": "New", "surprise": "x"}])

    assert _read(target) == [{"id": "1", "title": "Original"}]


@pytest.mark.unit
def test_empty_write_produces_a_header_only_file(tmp_path):
    """PRD 10: cleanup must never delete the file itself, only its rows."""
    target = tmp_path / "topics.csv"

    atomic_write_csv(target, FIELDS, [])

    assert target.exists()
    assert target.read_text(encoding="utf-8").strip() == "id,title"


@pytest.mark.unit
def test_backend_write_survives_a_failed_write(backend, data_dir):
    """End-to-end through the backend, not just the helper."""
    from tests.conftest import make_article

    backend.write_raw(Dataset.ARTICLES, [_row(make_article("2026-09-01T07:30:00+05:30"))])
    before = (data_dir / "articles.csv").read_text(encoding="utf-8")

    def exploding():
        yield _row(make_article("2026-09-01T08:00:00+05:30", article_id="a2"))
        raise RuntimeError("boom")

    with pytest.raises(BriefingError):
        backend.write_raw(Dataset.ARTICLES, exploding())

    assert (data_dir / "articles.csv").read_text(encoding="utf-8") == before


def _row(record):
    from app.storage.repository import _to_row

    return _to_row(record)
