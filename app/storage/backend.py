"""Storage backends.

`LocalCsvBackend` is the Phase 2 implementation. Phase 9 adds a git-backed backend
behind the same interface so `data/*.csv` survives a Render redeploy (PRD 8) --
nothing above this layer changes when that happens.
"""

import csv
import logging
import shutil
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from app.logging_setup import LOGGER_NAME
from app.storage.atomic import atomic_write_csv
from app.storage.datasets import Dataset, filename_for, spec_for


class StorageBackend(ABC):
    """Raw string-row access to a dataset. Typing and validation live in Repository."""

    @abstractmethod
    def read_raw(self, dataset: Dataset) -> list[dict[str, str]]: ...

    @abstractmethod
    def write_raw(self, dataset: Dataset, rows: Iterable[dict[str, str]]) -> int: ...


class LocalCsvBackend(StorageBackend):
    """CSV files in a local directory, written atomically.

    Read failures never raise. PRD 70 is explicit that a corrupt file must be preserved
    and the run allowed to continue -- yesterday's history is an input to ranking, not a
    precondition for producing today's briefing.
    """

    def __init__(self, data_dir: Path, logger: logging.Logger | None = None) -> None:
        self.data_dir = Path(data_dir)
        self._log = logger or logging.getLogger(LOGGER_NAME)

    def path_for(self, dataset: Dataset) -> Path:
        return self.data_dir / filename_for(dataset)

    def read_raw(self, dataset: Dataset) -> list[dict[str, str]]:
        path = self.path_for(dataset)
        if not path.exists():
            return []

        expected = spec_for(dataset).fieldnames
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    return []  # zero-byte file: empty, not corrupt
                if list(reader.fieldnames) != expected:
                    self._quarantine(
                        path,
                        f"header mismatch: {reader.fieldnames} != {expected}",
                    )
                    return []
                return [dict(row) for row in reader]

        except (UnicodeDecodeError, csv.Error) as exc:
            self._quarantine(path, f"unreadable: {exc}")
            return []

    def write_raw(self, dataset: Dataset, rows: Iterable[dict[str, str]]) -> int:
        return atomic_write_csv(self.path_for(dataset), spec_for(dataset).fieldnames, rows)

    def _quarantine(self, path: Path, reason: str) -> None:
        """Preserve the damaged file for forensics, then let the run continue (PRD 70).

        The original is left in place so a later write replaces it normally; the copy
        is what survives for inspection.
        """
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        recovery = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
        try:
            shutil.copy2(path, recovery)
            saved = recovery.name
        except OSError as exc:
            saved = f"<copy failed: {exc}>"

        self._log.error(
            "CSV_READ_FAILED file=%s reason=%s recovery_copy=%s", path.name, reason, saved
        )
