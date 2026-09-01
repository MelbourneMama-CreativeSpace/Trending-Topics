"""Phase 8: the briefing endpoint's contract (PRD 49, 73, 78).

The runner is injected, so these tests pin down what the endpoint promises callers --
status codes, response shape, and how the mode flags reach the pipeline -- without
running a pipeline. The pipeline itself is covered in test_pipeline.py.
"""

import pytest
from fastapi.testclient import TestClient

from app.errors import BriefingError, ErrorCode
from app.main import create_app, get_runner
from app.pipeline import RunOutcome
from app.run_context import is_valid_run_id

ENDPOINT = "/api/daily-brief"


class FakeRunner:
    """Records how it was called and returns a canned outcome."""

    def __init__(self, outcome: RunOutcome | None = None) -> None:
        self.outcome = outcome or RunOutcome(
            success=True, run_id="20260901-073000-a81f", status="completed",
            global_topics=5, niche_topics=5, email_sent=True, duration_seconds=143.2,
        )
        self.calls: list[dict] = []

    async def run(self, *, dry_run: bool = False, force: bool = False) -> RunOutcome:
        self.calls.append({"dry_run": dry_run, "force": force})
        return self.outcome


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def api(configured_env, runner):
    app = create_app()
    app.dependency_overrides[get_runner] = lambda: runner
    with TestClient(app) as client:
        yield client


# --- success (PRD 78) --------------------------------------------------------


@pytest.mark.unit
def test_successful_run_returns_the_prd_response_shape(api, auth_headers, runner):
    response = api.post(ENDPOINT, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "completed"
    assert body["global_topics"] == 5
    assert body["niche_topics"] == 5
    assert body["email_sent"] is True
    assert is_valid_run_id(body["run_id"])


@pytest.mark.unit
def test_partial_run_is_reported_as_partial(configured_env, auth_headers):
    """PRD 78: a partial briefing is still a success, but says so."""
    runner = FakeRunner(RunOutcome(
        success=True, run_id="20260901-073000-a81f", status="partial",
        global_topics=5, niche_topics=4, email_sent=True,
        warnings=["only 4 of 5 creative topics were verified"],
    ))
    app = create_app()
    app.dependency_overrides[get_runner] = lambda: runner

    with TestClient(app) as client:
        body = client.post(ENDPOINT, headers=auth_headers).json()

    assert body["success"] is True
    assert body["status"] == "partial"
    assert body["niche_topics"] == 4
    assert body["warnings"]


@pytest.mark.unit
def test_duplicate_run_returns_already_completed(configured_env, auth_headers):
    """PRD 73: idempotent for the day, and not an error."""
    runner = FakeRunner(RunOutcome(
        success=True, run_id="20260901-073000-a81f", status="already_completed",
    ))
    app = create_app()
    app.dependency_overrides[get_runner] = lambda: runner

    with TestClient(app) as client:
        response = client.post(ENDPOINT, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "already_completed"


# --- failure -----------------------------------------------------------------


@pytest.mark.unit
def test_failed_run_returns_500_with_the_error_code(configured_env, auth_headers):
    """A green tick on a morning with no email would be worse than a red one."""
    runner = FakeRunner(RunOutcome(
        success=False, run_id="20260901-073000-a81f", status="failed",
        error=ErrorCode.NO_USABLE_NEWS.value,
    ))
    app = create_app()
    app.dependency_overrides[get_runner] = lambda: runner

    with TestClient(app) as client:
        response = client.post(ENDPOINT, headers=auth_headers)

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "NO_USABLE_NEWS"


@pytest.mark.unit
def test_failure_response_omits_counts_it_does_not_have(configured_env, auth_headers):
    runner = FakeRunner(RunOutcome(
        success=False, run_id="r", status="failed", error="AI_PROCESSING_FAILED",
    ))
    app = create_app()
    app.dependency_overrides[get_runner] = lambda: runner

    with TestClient(app) as client:
        body = client.post(ENDPOINT, headers=auth_headers).json()

    assert "email_sent" not in body
    assert "global_topics" not in body


@pytest.mark.unit
def test_incomplete_configuration_returns_503_not_500(configured_env, auth_headers):
    """Missing credentials mean the service is not ready, not that it is broken."""
    def unconfigured():
        raise BriefingError(ErrorCode.CONFIG_INVALID, "missing configuration")

    app = create_app()
    app.dependency_overrides[get_runner] = unconfigured

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(ENDPOINT, headers=auth_headers)

    assert response.status_code == 503
    assert response.json()["error"] == "CONFIG_INVALID"


# --- modes (PRD 49, 86) ------------------------------------------------------


@pytest.mark.unit
def test_modes_default_to_off(api, auth_headers, runner):
    api.post(ENDPOINT, headers=auth_headers)

    assert runner.calls == [{"dry_run": False, "force": False}]


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [("true", True), ("false", False), ("1", True)])
def test_dry_run_reaches_the_pipeline(api, auth_headers, runner, raw, expected):
    api.post(f"{ENDPOINT}?dry_run={raw}", headers=auth_headers)

    assert runner.calls[0]["dry_run"] is expected


@pytest.mark.unit
def test_force_reaches_the_pipeline(api, auth_headers, runner):
    api.post(f"{ENDPOINT}?force=true", headers=auth_headers)

    assert runner.calls[0]["force"] is True


@pytest.mark.unit
def test_invalid_mode_value_is_a_422_not_a_silent_false(api, auth_headers, runner):
    """A typo like dry_run=maybe must not quietly send a real email."""
    response = api.post(f"{ENDPOINT}?dry_run=maybe", headers=auth_headers)

    assert response.status_code == 422
    assert runner.calls == [], "the pipeline must not run on a malformed request"


# --- other -------------------------------------------------------------------


@pytest.mark.unit
def test_response_contains_no_secret(api, auth_headers):
    from tests.conftest import TEST_AGENT_SECRET

    assert TEST_AGENT_SECRET not in api.post(ENDPOINT, headers=auth_headers).text


@pytest.mark.unit
def test_get_is_not_allowed(api, auth_headers):
    """The briefing is a POST -- a GET must not trigger a run."""
    assert api.get(ENDPOINT, headers=auth_headers).status_code == 405


@pytest.mark.unit
def test_unauthenticated_request_never_reaches_the_pipeline(api, runner):
    assert api.post(ENDPOINT).status_code == 401
    assert runner.calls == []
