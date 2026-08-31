"""Structured run logging (PRD 74, 75).

Every execution gets a `run_id` and emits a fixed vocabulary of events, so a run can
be reconstructed from Render's log stream without a tracing backend.

Output format matches PRD 75:

    [INFO] run_id=abc123 START dry_run=False force=False
    [INFO] run_id=abc123 COLLECTION_COMPLETED collected=183
"""

import logging
import sys
from enum import StrEnum

LOGGER_NAME = "briefing"
REDACTION_PLACEHOLDER = "***REDACTED***"

# Anything shorter than this is too generic to redact safely -- replacing a 3-character
# string everywhere would mangle unrelated log lines.
MIN_REDACTABLE_LENGTH = 8


class LogEvent(StrEnum):
    """The run event vocabulary (PRD 74), in pipeline order."""

    START = "START"
    AUTHENTICATED = "AUTHENTICATED"
    CLEANUP_STARTED = "CLEANUP_STARTED"
    COLLECTION_STARTED = "COLLECTION_STARTED"
    COLLECTION_COMPLETED = "COLLECTION_COMPLETED"
    DEDUP_COMPLETED = "DEDUP_COMPLETED"
    CLUSTERING_COMPLETED = "CLUSTERING_COMPLETED"
    GLOBAL_RANKING_COMPLETED = "GLOBAL_RANKING_COMPLETED"
    NICHE_RANKING_COMPLETED = "NICHE_RANKING_COMPLETED"
    RESEARCH_STARTED = "RESEARCH_STARTED"
    RESEARCH_COMPLETED = "RESEARCH_COMPLETED"
    AI_SUMMARY_COMPLETED = "AI_SUMMARY_COMPLETED"
    EMAIL_RENDERED = "EMAIL_RENDERED"
    EMAIL_SENT = "EMAIL_SENT"
    CLEANUP_COMPLETED = "CLEANUP_COMPLETED"
    END = "END"

    # Not in PRD 74's happy path, but every run that fails needs a terminal event.
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class SecretRedactionFilter(logging.Filter):
    """Last-resort guard against a secret reaching the log stream.

    `SecretStr` already prevents accidental interpolation, but a caller can always
    unwrap a value and log it by mistake. This catches that on the way out.
    """

    def __init__(self, secret_values: list[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secret_values if s and len(s) >= MIN_REDACTABLE_LENGTH]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True

        message = record.getMessage()
        redacted = message
        for secret in self._secrets:
            redacted = redacted.replace(secret, REDACTION_PLACEHOLDER)

        if redacted != message:
            # Collapse args into the already-formatted message so the replacement sticks.
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(secret_values: list[str] | None = None, level: int = logging.INFO) -> None:
    """Install the run logger. Idempotent -- safe to call on every app creation."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    # Telugu headlines reach the logs. A Windows console defaults to cp1252, which
    # cannot encode them -- logging swallows the UnicodeEncodeError and the line is
    # lost. Render is already UTF-8, so this is a no-op there.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    handler.addFilter(SecretRedactionFilter(secret_values or []))
    logger.addHandler(handler)


class RunLogger:
    """Binds a `run_id` to every line, so concurrent runs stay separable."""

    def __init__(self, run_id: str, logger: logging.Logger | None = None) -> None:
        self.run_id = run_id
        self._logger = logger or logging.getLogger(LOGGER_NAME)

    def event(self, event: LogEvent, level: int = logging.INFO, **fields: object) -> None:
        parts = [f"run_id={self.run_id}", str(event)]
        parts.extend(f"{key}={value}" for key, value in fields.items())
        self._logger.log(level, " ".join(parts))

    def warning(self, event: LogEvent, **fields: object) -> None:
        self.event(event, level=logging.WARNING, **fields)

    def error(self, event: LogEvent, **fields: object) -> None:
        self.event(event, level=logging.ERROR, **fields)
