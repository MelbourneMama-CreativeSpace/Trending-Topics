"""Single-execution guard (PRD 71).

Only one briefing should run at a time -- concurrent runs would interleave CSV writes
and could send two emails for the same day.

The lock is a file created with `O_CREAT | O_EXCL`, which is atomic: exactly one caller
can win the race. A stale lock (from a process killed before it could release) is broken
after `ttl_seconds`, otherwise one crash would block every subsequent morning.
"""

import logging
import os
import time
from pathlib import Path
from types import TracebackType

from app.errors import BriefingError, ErrorCode, Severity
from app.logging_setup import LOGGER_NAME

# Comfortably beyond the PRD 81 target of a run finishing in under 5 minutes, so a
# healthy-but-slow run is never mistaken for a dead one.
DEFAULT_LOCK_TTL_SECONDS = 900


class ExecutionLock:
    """Context manager around a lock file.

    Raises `BriefingError(RUN_ALREADY_IN_PROGRESS)` if another run holds it.
    """

    def __init__(
        self,
        path: Path,
        run_id: str,
        ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.ttl_seconds = ttl_seconds
        self._log = logger or logging.getLogger(LOGGER_NAME)
        self._held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._create()
        except FileExistsError:
            if not self._break_if_stale():
                raise BriefingError(
                    ErrorCode.RUN_ALREADY_IN_PROGRESS,
                    f"Another run holds {self.path.name}",
                    severity=Severity.CRITICAL,
                ) from None
            # The stale holder is gone; a second failure here is a genuine race with
            # another live process, so let it surface.
            try:
                self._create()
            except FileExistsError:
                raise BriefingError(
                    ErrorCode.RUN_ALREADY_IN_PROGRESS,
                    f"Lost race for {self.path.name}",
                    severity=Severity.CRITICAL,
                ) from None
        self._held = True

    def release(self) -> None:
        """Release only a lock we actually hold, so we never delete someone else's."""
        if not self._held:
            return
        self.path.unlink(missing_ok=True)
        self._held = False

    def _create(self) -> None:
        descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"run_id={self.run_id}\npid={os.getpid()}\nstarted={time.time()}\n")

    def _break_if_stale(self) -> bool:
        """Remove an abandoned lock. Returns True if one was cleared."""
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return True  # holder released between our attempt and this check

        if age < self.ttl_seconds:
            return False

        self._log.warning(
            "STALE_LOCK_CLEARED file=%s age_seconds=%d ttl_seconds=%d",
            self.path.name,
            int(age),
            self.ttl_seconds,
        )
        self.path.unlink(missing_ok=True)
        return True

    def __enter__(self) -> "ExecutionLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Released on every exit path, including exceptions -- PRD 90 requires the lock
        # never be orphaned by a crash.
        self.release()
