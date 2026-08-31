"""Phase 1: settings load, validate, and refuse to boot when unsafe."""

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_TIMEZONE, Settings, get_settings
from tests.conftest import TEST_AGENT_SECRET


@pytest.mark.unit
def test_defaults_match_prd(configured_env):
    settings = get_settings()

    assert settings.timezone == DEFAULT_TIMEZONE == "Asia/Kolkata"
    assert settings.global_top_n == 5
    assert settings.niche_top_n == 5
    assert settings.data_retention_days == 31
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"


@pytest.mark.unit
def test_env_overrides_defaults(configured_env):
    configured_env.setenv("GLOBAL_TOP_N", "3")
    configured_env.setenv("DATA_RETENTION_DAYS", "7")
    configured_env.setenv("OPENROUTER_MODEL", "moonshotai/kimi-k2-0905")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.global_top_n == 3
    assert settings.data_retention_days == 7
    assert settings.openrouter_model == "moonshotai/kimi-k2-0905"


@pytest.mark.unit
def test_missing_agent_secret_is_fatal(clean_env):
    """The endpoint is unauthenticated without it, so booting must fail."""
    with pytest.raises(ValidationError, match="agent_secret"):
        Settings()


@pytest.mark.unit
def test_short_agent_secret_is_rejected(clean_env):
    """A placeholder like 'changeme' must not be accepted as a security boundary."""
    clean_env.setenv("AGENT_SECRET", "short")

    with pytest.raises(ValidationError, match="at least 16 characters"):
        Settings()


@pytest.mark.unit
def test_unresolvable_timezone_is_rejected(configured_env):
    """Catches both a typo and a missing tzdata package, at startup not mid-run."""
    configured_env.setenv("TIMEZONE", "Mars/Olympus_Mons")

    with pytest.raises(ValidationError, match="could not be resolved"):
        Settings()


@pytest.mark.unit
def test_tz_property_returns_ist_offset(configured_env):
    from datetime import datetime

    settings = get_settings()
    offset = datetime(2026, 9, 1, 7, 30, tzinfo=settings.tz).utcoffset()

    assert offset.total_seconds() == 5.5 * 3600


@pytest.mark.unit
def test_out_of_range_top_n_is_rejected(configured_env):
    configured_env.setenv("GLOBAL_TOP_N", "0")

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
def test_missing_pipeline_config_reports_everything_absent(configured_env):
    """All missing keys at once, so the operator fixes them in one pass."""
    settings = get_settings()

    assert settings.missing_pipeline_config() == [
        "EMAIL_API_KEY",
        "OPENROUTER_API_KEY",
        "RECIPIENT_EMAIL",
        "SENDER_EMAIL",
    ]


@pytest.mark.unit
def test_missing_pipeline_config_empty_when_fully_configured(configured_env):
    configured_env.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    configured_env.setenv("EMAIL_API_KEY", "re_test_key")
    configured_env.setenv("SENDER_EMAIL", "brief@example.com")
    configured_env.setenv("RECIPIENT_EMAIL", "founder@example.com")
    get_settings.cache_clear()

    assert get_settings().missing_pipeline_config() == []


@pytest.mark.unit
def test_secrets_do_not_leak_through_repr(configured_env):
    """SecretStr must mask on str(), repr(), and the model dump used by tracebacks."""
    configured_env.setenv("OPENROUTER_API_KEY", "sk-or-super-secret")
    get_settings.cache_clear()

    settings = get_settings()
    rendered = f"{settings!r} {settings.model_dump()}"

    assert TEST_AGENT_SECRET not in rendered
    assert "sk-or-super-secret" not in rendered


@pytest.mark.unit
def test_secret_values_collects_only_configured_secrets(configured_env):
    configured_env.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    get_settings.cache_clear()

    values = get_settings().secret_values()

    assert TEST_AGENT_SECRET in values
    assert "sk-or-test-key" in values
    assert None not in values
    assert len(values) == 2
