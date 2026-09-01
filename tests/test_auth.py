"""Phase 1: the briefing endpoint is correctly protected (PRD 50).

The core requirement is that a rejection is *opaque*: nothing in the response may
tell an attacker whether AGENT_SECRET is set, how long it is, or how close a guess was.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import TEST_AGENT_SECRET

ENDPOINT = "/api/daily-brief"


@pytest.mark.unit
def test_no_authorization_header_is_rejected(client):
    assert client.post(ENDPOINT).status_code == 401


@pytest.mark.unit
def test_malformed_header_without_scheme_is_rejected(client):
    response = client.post(ENDPOINT, headers={"Authorization": TEST_AGENT_SECRET})

    assert response.status_code == 401


@pytest.mark.unit
def test_wrong_scheme_is_rejected(client):
    response = client.post(ENDPOINT, headers={"Authorization": f"Basic {TEST_AGENT_SECRET}"})

    assert response.status_code == 401


@pytest.mark.unit
def test_empty_bearer_token_is_rejected(client):
    response = client.post(ENDPOINT, headers={"Authorization": "Bearer "})

    assert response.status_code == 401


@pytest.mark.unit
def test_wrong_token_is_rejected(client):
    response = client.post(ENDPOINT, headers={"Authorization": "Bearer wrong-token-0123456789"})

    assert response.status_code == 401


@pytest.mark.unit
def test_token_prefix_is_rejected(client):
    """A correct prefix must not be accepted -- guards against a truncated comparison."""
    response = client.post(ENDPOINT, headers={"Authorization": f"Bearer {TEST_AGENT_SECRET[:10]}"})

    assert response.status_code == 401


@pytest.mark.unit
def test_correct_token_is_accepted(client, auth_headers):
    """Anything other than 401 means authentication passed.

    The endpoint now runs the real pipeline, which is unconfigured in tests and
    answers 503. That is a readiness problem downstream of auth, not an auth failure.
    """
    response = client.post(ENDPOINT, headers=auth_headers)

    assert response.status_code != 401


@pytest.mark.unit
def test_rejection_reveals_nothing_about_the_secret(client):
    response = client.post(ENDPOINT, headers={"Authorization": "Bearer wrong-token-0123456789"})
    body = response.text

    assert TEST_AGENT_SECRET not in body
    assert "AGENT_SECRET" not in body
    for leaky in ("length", "expected", "configured", "env"):
        assert leaky not in body.lower()


@pytest.mark.unit
def test_all_rejections_are_indistinguishable(client):
    """Missing, malformed, and wrong tokens must produce byte-identical responses.

    Any difference turns the endpoint into an oracle for probing configuration.
    """
    responses = [
        client.post(ENDPOINT),
        client.post(ENDPOINT, headers={"Authorization": "Bearer wrong-token-0123456789"}),
        client.post(ENDPOINT, headers={"Authorization": "Basic anything"}),
        client.post(ENDPOINT, headers={"Authorization": "garbage"}),
    ]

    assert {r.status_code for r in responses} == {401}
    assert len({r.text for r in responses}) == 1


@pytest.mark.unit
def test_query_params_do_not_bypass_auth(client):
    """force=true must never be reachable unauthenticated (PRD 49)."""
    response = client.post(f"{ENDPOINT}?force=true&dry_run=true")

    assert response.status_code == 401


@pytest.mark.unit
def test_auth_uses_the_configured_secret_not_a_constant(clean_env):
    """Swapping AGENT_SECRET must change which token works."""
    clean_env.setenv("AGENT_SECRET", "a-completely-different-secret-value")

    with TestClient(create_app()) as test_client:
        assert test_client.post(
            ENDPOINT, headers={"Authorization": f"Bearer {TEST_AGENT_SECRET}"}
        ).status_code == 401
        assert test_client.post(
            ENDPOINT, headers={"Authorization": "Bearer a-completely-different-secret-value"}
        ).status_code != 401
