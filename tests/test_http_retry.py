"""Phase 3: timeouts and the selective retry policy (PRD 63, 64)."""

import httpx
import pytest
import respx

from app.collect.http import DEFAULT_TIMEOUT_SECONDS, build_client, fetch, is_retryable_status

URL = "https://example.com/feed.xml"


@pytest.mark.unit
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_statuses_are_retryable(status):
    assert is_retryable_status(status) is True


@pytest.mark.unit
@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 410, 422])
def test_permanent_statuses_are_not_retryable(status):
    """Retrying a bad key or a dead URL only burns the run's time budget."""
    assert is_retryable_status(status) is False


@pytest.mark.integration
@respx.mock
async def test_successful_fetch_makes_one_request():
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=b"<rss/>"))

    async with build_client() as client:
        result = await fetch(client, URL, backoff_seconds=0)

    assert result.ok is True
    assert result.content == b"<rss/>"
    assert route.call_count == 1


@pytest.mark.integration
@respx.mock
async def test_rate_limit_is_retried_up_to_the_attempt_limit():
    route = respx.get(URL).mock(return_value=httpx.Response(429))

    async with build_client() as client:
        result = await fetch(client, URL, attempts=3, backoff_seconds=0)

    assert result.ok is False
    assert route.call_count == 3, "429 must be retried"


@pytest.mark.integration
@respx.mock
async def test_retry_succeeds_after_a_transient_failure():
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, content=b"<rss/>")]
    )

    async with build_client() as client:
        result = await fetch(client, URL, attempts=3, backoff_seconds=0)

    assert result.ok is True
    assert result.attempts == 2
    assert route.call_count == 2


@pytest.mark.integration
@respx.mock
@pytest.mark.parametrize("status", [401, 404])
async def test_permanent_failures_are_not_retried(status):
    """A single attempt, then give up -- PRD 64."""
    route = respx.get(URL).mock(return_value=httpx.Response(status))

    async with build_client() as client:
        result = await fetch(client, URL, attempts=3, backoff_seconds=0)

    assert result.ok is False
    assert route.call_count == 1, f"HTTP {status} must not be retried"


@pytest.mark.integration
@respx.mock
async def test_timeouts_are_retried():
    route = respx.get(URL).mock(side_effect=httpx.ReadTimeout("too slow"))

    async with build_client() as client:
        result = await fetch(client, URL, attempts=3, backoff_seconds=0)

    assert result.ok is False
    assert "ReadTimeout" in result.error
    assert route.call_count == 3


@pytest.mark.integration
@respx.mock
async def test_connection_errors_are_retried():
    route = respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))

    async with build_client() as client:
        result = await fetch(client, URL, attempts=2, backoff_seconds=0)

    assert result.ok is False
    assert route.call_count == 2


@pytest.mark.integration
@respx.mock
async def test_fetch_never_raises_on_failure():
    """A dead source is data, not an exception -- PRD 58 depends on this."""
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))

    async with build_client() as client:
        result = await fetch(client, URL, attempts=1, backoff_seconds=0)

    assert result.ok is False


@pytest.mark.unit
def test_client_has_a_bounded_timeout():
    """PRD 63: no request may hang indefinitely."""
    client = build_client()

    assert client.timeout.read == DEFAULT_TIMEOUT_SECONDS
    assert 10 <= DEFAULT_TIMEOUT_SECONDS <= 20
