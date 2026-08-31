"""Error codes and severities (PRD 56, 57).

Every failure in this system carries a machine-readable code. The code is what goes
into `briefings.csv` and the API response; the human message stays in the logs.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Internal error codes (PRD 56, extended with codes named elsewhere in the spec)."""

    # --- Auth / request ---
    AUTH_FAILED = "AUTH_FAILED"

    # --- Collection (PRD 56, 59) ---
    NO_GLOBAL_NEWS = "NO_GLOBAL_NEWS"
    NO_NICHE_NEWS = "NO_NICHE_NEWS"
    NO_USABLE_NEWS = "NO_USABLE_NEWS"
    NEWS_PROVIDER_FAILED = "NEWS_PROVIDER_FAILED"
    SEARCH_PROVIDER_FAILED = "SEARCH_PROVIDER_FAILED"

    # --- AI (PRD 56, 60) ---
    AI_TIMEOUT = "AI_TIMEOUT"
    AI_RATE_LIMITED = "AI_RATE_LIMITED"
    AI_INVALID_RESPONSE = "AI_INVALID_RESPONSE"
    AI_PROCESSING_FAILED = "AI_PROCESSING_FAILED"

    # --- Output ---
    SOURCE_VALIDATION_FAILED = "SOURCE_VALIDATION_FAILED"
    EMAIL_FAILED = "EMAIL_FAILED"

    # --- Persistence ---
    CSV_READ_FAILED = "CSV_READ_FAILED"
    CSV_WRITE_FAILED = "CSV_WRITE_FAILED"

    # --- Execution control (PRD 48, 71, 72) ---
    DUPLICATE_BRIEFING = "DUPLICATE_BRIEFING"
    RUN_ALREADY_IN_PROGRESS = "RUN_ALREADY_IN_PROGRESS"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"

    # --- Startup / catch-all ---
    CONFIG_INVALID = "CONFIG_INVALID"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class Severity(StrEnum):
    """Failure severity (PRD 57), which decides whether the pipeline continues."""

    WARNING = "WARNING"  # one source failed; pipeline continues
    ERROR = "ERROR"  # one topic failed; pipeline continues
    CRITICAL = "CRITICAL"  # no usable news; pipeline stops
    FATAL = "FATAL"  # cannot initialise; request fails


class BriefingError(Exception):
    """Raised when a run cannot continue. Carries the code recorded in briefings.csv."""

    def __init__(
        self,
        code: ErrorCode,
        message: str = "",
        severity: Severity = Severity.CRITICAL,
    ) -> None:
        self.code = code
        self.severity = severity
        super().__init__(message or str(code))
