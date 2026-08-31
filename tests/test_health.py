"""Phase 1: the health endpoint (PRD 51)."""

import pytest

from tests.conftest import TEST_AGENT_SECRET


@pytest.mark.unit
def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.unit
def test_health_needs_no_authentication(client):
    """Render's probe cannot present a bearer token."""
    assert client.get("/health").status_code == 200


@pytest.mark.unit
def test_health_exposes_no_configuration(client):
    """PRD 51: it must not leak secrets -- and nothing else useful either."""
    body = client.get("/health").text

    assert TEST_AGENT_SECRET not in body
    for leaky in ("secret", "key", "token", "email", "openrouter", "timezone"):
        assert leaky not in body.lower()
