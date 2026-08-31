"""Phase 1: structured run logging and secret redaction (PRD 74, 75, 91)."""

import logging

import pytest

from app.logging_setup import (
    LOGGER_NAME,
    REDACTION_PLACEHOLDER,
    LogEvent,
    RunLogger,
    SecretRedactionFilter,
    configure_logging,
)


@pytest.mark.unit
def test_run_logger_emits_prd_line_format(run_log):
    """PRD 75: [INFO] run_id=abc123 COLLECTION_COMPLETED collected=183"""
    RunLogger("abc123").event(LogEvent.COLLECTION_COMPLETED, collected=183)

    assert run_log.messages == ["run_id=abc123 COLLECTION_COMPLETED collected=183"]


@pytest.mark.unit
def test_run_logger_binds_run_id_to_every_line(run_log):
    log = RunLogger("run-42")
    log.event(LogEvent.START)
    log.event(LogEvent.END)

    assert len(run_log.messages) == 2, "guard against a vacuous all() over no records"
    assert all(message.startswith("run_id=run-42 ") for message in run_log.messages)


@pytest.mark.unit
def test_error_and_warning_levels_are_distinguishable(run_log):
    log = RunLogger("run-42")
    log.warning(LogEvent.SKIPPED, reason="source_unavailable")
    log.error(LogEvent.FAILED, error_code="NO_USABLE_NEWS")

    levels = [record.levelno for record in run_log.records]
    assert levels == [logging.WARNING, logging.ERROR]


@pytest.mark.unit
def test_prd_event_vocabulary_is_complete():
    """PRD 74 lists the events a run must emit."""
    required = {
        "START", "AUTHENTICATED", "CLEANUP_STARTED", "COLLECTION_STARTED",
        "COLLECTION_COMPLETED", "DEDUP_COMPLETED", "CLUSTERING_COMPLETED",
        "GLOBAL_RANKING_COMPLETED", "NICHE_RANKING_COMPLETED", "RESEARCH_STARTED",
        "RESEARCH_COMPLETED", "AI_SUMMARY_COMPLETED", "EMAIL_RENDERED", "EMAIL_SENT",
        "CLEANUP_COMPLETED", "END",
    }

    assert required <= {event.value for event in LogEvent}


@pytest.mark.unit
def test_redaction_filter_removes_a_leaked_secret():
    """Last line of defence if a caller unwraps a SecretStr and logs it."""
    record = logging.LogRecord(
        name=LOGGER_NAME, level=logging.INFO, pathname="", lineno=0,
        msg="calling openrouter with key=sk-or-super-secret-value", args=(), exc_info=None,
    )

    SecretRedactionFilter(["sk-or-super-secret-value"]).filter(record)

    assert "sk-or-super-secret-value" not in record.getMessage()
    assert REDACTION_PLACEHOLDER in record.getMessage()


@pytest.mark.unit
def test_redaction_survives_lazy_percent_formatting():
    """A secret passed as a logging arg, not baked into msg, must still be caught."""
    record = logging.LogRecord(
        name=LOGGER_NAME, level=logging.INFO, pathname="", lineno=0,
        msg="token=%s", args=("sk-or-super-secret-value",), exc_info=None,
    )

    SecretRedactionFilter(["sk-or-super-secret-value"]).filter(record)

    assert "sk-or-super-secret-value" not in record.getMessage()


@pytest.mark.unit
def test_short_values_are_not_redacted():
    """Redacting a 3-character string would mangle unrelated lines."""
    record = logging.LogRecord(
        name=LOGGER_NAME, level=logging.INFO, pathname="", lineno=0,
        msg="collected=183 from abc", args=(), exc_info=None,
    )

    SecretRedactionFilter(["abc"]).filter(record)

    assert record.getMessage() == "collected=183 from abc"


@pytest.mark.unit
def test_configure_logging_is_idempotent():
    """App creation must not stack duplicate handlers and double every line."""
    configure_logging(["sk-or-super-secret-value"])
    configure_logging(["sk-or-super-secret-value"])

    assert len(logging.getLogger(LOGGER_NAME).handlers) == 1
