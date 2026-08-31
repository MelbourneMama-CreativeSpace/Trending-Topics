"""Shared fixtures.

Two invariants every test relies on:

* No test ever reads a developer's real `.env`. `_isolate_dotenv` disables the file
  so a machine with real credentials produces the same results as a bare CI runner.
* `get_settings` is cached, so its cache is cleared around every test that changes
  the environment.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app

# Long enough to satisfy MIN_AGENT_SECRET_LENGTH.
TEST_AGENT_SECRET = "test-agent-secret-0123456789"

# Every env var Settings reads. Cleared per-test so the host environment cannot leak in.
APP_ENV_VARS = (
    "AGENT_SECRET",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_BASE_URL",
    "EMAIL_API_KEY",
    "SENDER_EMAIL",
    "RECIPIENT_EMAIL",
    "TIMEZONE",
    "GLOBAL_TOP_N",
    "NICHE_TOP_N",
    "DATA_RETENTION_DAYS",
    "NEWS_API_KEY",
    "SEARCH_API_KEY",
    "GITHUB_TOKEN",
    "GITHUB_DATA_REPO",
    "GITHUB_DATA_BRANCH",
)


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch):
    """Stop Settings reading a real .env during tests."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def clean_env(monkeypatch):
    """A pristine environment with no app variables set at all."""
    for name in APP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def configured_env(clean_env):
    """Minimum viable environment: the app boots, the pipeline is not yet configured."""
    clean_env.setenv("AGENT_SECRET", TEST_AGENT_SECRET)
    return clean_env


@pytest.fixture
def client(configured_env):
    """A booted app. The context manager runs lifespan, as production does."""
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {TEST_AGENT_SECRET}"}


@pytest.fixture
def run_log(caplog):
    """Capture from the `briefing` logger.

    `configure_logging` sets `propagate = False` so uvicorn's root handler does not
    print every line twice. That also hides records from caplog, which listens on the
    root logger -- so attach caplog's handler to ours directly.
    """
    import logging

    from app.logging_setup import LOGGER_NAME

    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
