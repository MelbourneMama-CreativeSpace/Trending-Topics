"""Typed persistence, retention and idempotency (PRD 9, 10, 48, 70, 73).

The repository owns three responsibilities the raw backend does not:

* converting between models and CSV string rows
* the 31-day cutoff, computed in the briefing timezone
* the briefing record that makes a day's run idempotent
"""

import datetime as dt
import logging
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from app.logging_setup import LOGGER_NAME
from app.models import Briefing
from app.storage.backend import StorageBackend
from app.storage.datasets import Dataset, DatasetSpec, spec_for


@dataclass
class CleanupReport:
    """What the 31-day sweep removed (PRD 10)."""

    cutoff_date: dt.date
    removed: dict[Dataset, int] = field(default_factory=dict)

    @property
    def total_removed(self) -> int:
        return sum(self.removed.values())


class Repository:
    def __init__(
        self,
        backend: StorageBackend,
        tz: ZoneInfo,
        retention_days: int = 31,
        logger: logging.Logger | None = None,
    ) -> None:
        self._backend = backend
        self._tz = tz
        self._retention_days = retention_days
        self._log = logger or logging.getLogger(LOGGER_NAME)

    # --- typed read / write -------------------------------------------------

    def read(self, dataset: Dataset) -> list[BaseModel]:
        """Load and validate rows.

        A row that fails validation is skipped rather than aborting the read: one bad
        record from an older schema must not cost us the whole file.
        """
        spec = spec_for(dataset)
        records: list[BaseModel] = []
        skipped = 0

        for raw in self._backend.read_raw(dataset):
            try:
                records.append(spec.model.model_validate(_clean_row(raw, spec)))
            except ValidationError:
                skipped += 1

        if skipped:
            self._log.warning(
                "CSV_ROW_SKIPPED dataset=%s skipped=%d kept=%d",
                dataset.value,
                skipped,
                len(records),
            )
        return records

    def write(self, dataset: Dataset, records: list[BaseModel]) -> int:
        return self._backend.write_raw(dataset, (_to_row(r) for r in records))

    def append(self, dataset: Dataset, records: list[BaseModel]) -> int:
        """Append by rewriting the whole file -- atomically, so it is still crash-safe.

        These files hold roughly a month of rows, so a full rewrite is cheap and keeps
        one write path instead of two.
        """
        if not records:
            return 0
        return self.write(dataset, [*self.read(dataset), *records])

    # --- retention (PRD 9, 10) ---------------------------------------------

    def cutoff_date(self, now: dt.datetime | None = None) -> dt.date:
        """PRD 9: on September 1 with a 31-day window, delete records before August 1.

        Computed from the local date in the briefing timezone. Using UTC here would
        drop a row stamped 00:30 IST, whose UTC date is still the previous day.
        """
        moment = now.astimezone(self._tz) if now else dt.datetime.now(self._tz)
        return moment.date() - dt.timedelta(days=self._retention_days)

    def cleanup_old_data(self, now: dt.datetime | None = None) -> CleanupReport:
        """Delete expired rows. Never deletes the files themselves (PRD 10)."""
        cutoff = self.cutoff_date(now)
        report = CleanupReport(cutoff_date=cutoff)

        for dataset in Dataset:
            spec = spec_for(dataset)
            if spec.retention_field is None:
                continue

            rows = self._backend.read_raw(dataset)
            if not rows:
                continue

            kept = [r for r in rows if self._is_within_retention(r, spec, cutoff, dataset)]
            removed = len(rows) - len(kept)
            if removed:
                self._backend.write_raw(dataset, kept)
                report.removed[dataset] = removed

        return report

    def _is_within_retention(
        self, row: dict[str, str], spec: DatasetSpec, cutoff: dt.date, dataset: Dataset
    ) -> bool:
        local_date = _local_date(row.get(spec.retention_field, ""), self._tz)
        if local_date is None:
            # Unparseable timestamp: keep it. Deleting data we cannot read is worse
            # than carrying a row we cannot date.
            self._log.warning(
                "RETENTION_UNPARSEABLE dataset=%s field=%s value=%r",
                dataset.value,
                spec.retention_field,
                row.get(spec.retention_field, ""),
            )
            return True
        return local_date >= cutoff

    # --- idempotency (PRD 48, 73) ------------------------------------------

    def find_briefing(self, briefing_id: str) -> Briefing | None:
        for record in self.read(Dataset.BRIEFINGS):
            if isinstance(record, Briefing) and record.briefing_id == briefing_id:
                return record
        return None

    def was_already_delivered(self, briefing_id: str) -> bool:
        """PRD 48: has today's briefing already been sent successfully?"""
        existing = self.find_briefing(briefing_id)
        return existing is not None and existing.was_delivered()

    def upsert_briefing(self, briefing: Briefing) -> None:
        """Insert or replace by `briefing_id`, so a run updates its own record."""
        records = [
            r
            for r in self.read(Dataset.BRIEFINGS)
            if not (isinstance(r, Briefing) and r.briefing_id == briefing.briefing_id)
        ]
        records.append(briefing)
        self.write(Dataset.BRIEFINGS, records)


# --- row conversion ---------------------------------------------------------


def _to_row(record: BaseModel) -> dict[str, str]:
    """Model -> CSV row. None becomes an empty cell."""
    dumped = record.model_dump(mode="json")
    return {key: "" if value is None else str(value) for key, value in dumped.items()}


def _clean_row(row: dict[str, str], spec: DatasetSpec) -> dict[str, object]:
    """CSV row -> validation input. Empty cells become None only where None is legal."""
    return {
        key: None if (value == "" and key in spec.nullable_fields) else value
        for key, value in row.items()
    }


def _local_date(value: str, tz: ZoneInfo) -> dt.date | None:
    """Interpret a stored date or datetime as a calendar date in the briefing zone."""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        # A naive stored timestamp predates the tz-aware writer; assume briefing zone.
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz).date()
