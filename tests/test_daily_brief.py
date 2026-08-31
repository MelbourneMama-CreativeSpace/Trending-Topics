"""Phase 1: the briefing endpoint's request envelope (PRD 49, 78, 86).

The pipeline is Phase 8. What is verified here is that the endpoint accepts the
request correctly, reports modes honestly, and does not claim to have done work.
"""

import pytest

from app.run_context import is_valid_run_id

ENDPOINT = "/api/daily-brief"


@pytest.mark.unit
def test_response_carries_a_valid_run_id(client, auth_headers):
    body = client.post(ENDPOINT, headers=auth_headers).json()

    assert is_valid_run_id(body["run_id"])


@pytest.mark.unit
def test_each_request_gets_a_distinct_run_id(client, auth_headers):
    first = client.post(ENDPOINT, headers=auth_headers).json()["run_id"]
    second = client.post(ENDPOINT, headers=auth_headers).json()["run_id"]

    assert first != second


@pytest.mark.unit
def test_modes_default_to_off(client, auth_headers):
    body = client.post(ENDPOINT, headers=auth_headers).json()

    assert body["dry_run"] is False
    assert body["force"] is False


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [("true", True), ("false", False), ("1", True)])
def test_dry_run_query_param_is_parsed(client, auth_headers, raw, expected):
    body = client.post(f"{ENDPOINT}?dry_run={raw}", headers=auth_headers).json()

    assert body["dry_run"] is expected


@pytest.mark.unit
def test_force_query_param_is_parsed(client, auth_headers):
    body = client.post(f"{ENDPOINT}?force=true", headers=auth_headers).json()

    assert body["force"] is True


@pytest.mark.unit
def test_invalid_mode_value_is_a_422_not_a_silent_false(client, auth_headers):
    """A typo like dry_run=yes must not quietly send a real email."""
    response = client.post(f"{ENDPOINT}?dry_run=maybe", headers=auth_headers)

    assert response.status_code == 422


@pytest.mark.unit
def test_stub_does_not_claim_to_have_sent_anything(client, auth_headers):
    """Until Phase 8 exists, the response must be honest about doing nothing."""
    body = client.post(ENDPOINT, headers=auth_headers).json()

    assert body["pipeline"] == "not_implemented"
    assert "email_sent" not in body


@pytest.mark.unit
def test_response_reports_missing_pipeline_config(client, auth_headers):
    """Operator-facing: names of absent env vars, never their values."""
    body = client.post(ENDPOINT, headers=auth_headers).json()

    assert "OPENROUTER_API_KEY" in body["missing_pipeline_config"]
    assert "EMAIL_API_KEY" in body["missing_pipeline_config"]


@pytest.mark.unit
def test_response_contains_no_secret(client, auth_headers):
    from tests.conftest import TEST_AGENT_SECRET

    assert TEST_AGENT_SECRET not in client.post(ENDPOINT, headers=auth_headers).text


@pytest.mark.unit
def test_get_is_not_allowed(client, auth_headers):
    """The briefing is a POST -- a GET must not trigger a run."""
    assert client.get(ENDPOINT, headers=auth_headers).status_code == 405
