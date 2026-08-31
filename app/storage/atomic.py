"""Crash-safe CSV writes (PRD 11).

    write temporary file -> validate -> replace original

If the process dies at any point before `os.replace`, the original file is untouched.
`os.replace` is atomic on both POSIX and Windows when source and destination are on the
same volume, which they are: the temp file is written beside its target.
"""

import csv
import os
from collections.abc import Iterable
from pathlib import Path

from app.errors import BriefingError, ErrorCode, Severity


def temp_path_for(path: Path) -> Path:
    """PRD 11 names the sidecar `topics.tmp.csv` for `topics.csv`."""
    return path.with_name(f"{path.stem}.tmp{path.suffix}")


def atomic_write_csv(
    path: Path,
    fieldnames: list[str],
    rows: Iterable[dict[str, str]],
) -> int:
    """Write `rows` to `path`, atomically. Returns the number of data rows written.

    Raises `BriefingError(CSV_WRITE_FAILED)` without touching the original file if
    serialisation fails partway or the written file fails validation.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_path_for(path)

    try:
        written = 0
        with tmp.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
                written += 1
            handle.flush()
            # Without fsync the bytes may still be in the OS cache when replace() runs,
            # so a power loss could leave a valid-looking but empty file.
            os.fsync(handle.fileno())

        _validate(tmp, fieldnames, written)
        os.replace(tmp, path)
        return written

    except BriefingError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        # Serialisation blew up mid-stream. The temp file is incomplete; the original
        # is still whole because we have not replaced it.
        tmp.unlink(missing_ok=True)
        raise BriefingError(
            ErrorCode.CSV_WRITE_FAILED,
            f"Failed writing {path.name}: {exc}",
            severity=Severity.CRITICAL,
        ) from exc


def _validate(tmp: Path, fieldnames: list[str], expected_rows: int) -> None:
    """Read the temp file back before trusting it as the new original."""
    with tmp.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != fieldnames:
            raise BriefingError(
                ErrorCode.CSV_WRITE_FAILED,
                f"{tmp.name} header mismatch: {reader.fieldnames} != {fieldnames}",
            )
        actual = sum(1 for _ in reader)

    if actual != expected_rows:
        raise BriefingError(
            ErrorCode.CSV_WRITE_FAILED,
            f"{tmp.name} row count mismatch: wrote {expected_rows}, read back {actual}",
        )
