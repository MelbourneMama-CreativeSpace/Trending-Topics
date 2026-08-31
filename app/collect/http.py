"""Shared HTTP fetching with timeouts and a selective retry policy (PRD 63, 64).

Failures are returned, not raised. A dead feed is ordinary data about the world, and
PRD 58 requires the pipeline to carry on collecting from everything else.
"""

import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.logging_setup import LOGGER_NAME

# PRD 63: no request may hang indefinitely.
DEFAULT_TIMEOUT_SECONDS = 15.0

# PRD 64: transient. Worth another attempt.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# PRD 64: permanent. Retrying a bad key or a dead URL only wastes the run's budget.
NON_RETRYABLE_STATUS = frozenset({400, 401, 403, 404, 405, 410, 422})

DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 1.0

USER_AGENT = "MelbourneMamaMorningIntelligence/0.1 (+daily briefing agent)"


@dataclass
class FetchResult:
    """Outcome of one fetch. `ok` is the only thing callers need to branch on."""

    url: str
    ok: bool
    status: int | None = None
    content: bytes = b""
    error: str = ""
    attempts: int = 1

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def is_retryable_status(status: int) -> bool:
    if status in NON_RETRYABLE_STATUS:
        return False
    return status in RETRYABLE_STATUS


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    headers: dict[str, str] | None = None,
    logger: logging.Logger | None = None,
) -> FetchResult:
    """GET `url`, retrying only transient failures."""
    log = logger or logging.getLogger(LOGGER_NAME)
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    last = FetchResult(url=url, ok=False, error="no attempt made")

    for attempt in range(1, attempts + 1):
        try:
            response = await client.get(url, headers=request_headers, follow_redirects=True)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Timeouts and connection resets are transient by PRD 64.
            last = FetchResult(
                url=url, ok=False, error=f"{type(exc).__name__}: {exc}", attempts=attempt
            )
        else:
            if response.status_code < 400:
                return FetchResult(
                    url=url,
                    ok=True,
                    status=response.status_code,
                    content=response.content,
                    attempts=attempt,
                )

            last = FetchResult(
                url=url,
                ok=False,
                status=response.status_code,
                error=f"HTTP {response.status_code}",
                attempts=attempt,
            )
            if not is_retryable_status(response.status_code):
                log.warning("FETCH_FAILED_PERMANENT url=%s status=%d", url, response.status_code)
                return last

        if attempt < attempts:
            # Exponential backoff: 1s, then 2s. Bounded by DEFAULT_ATTEMPTS.
            await asyncio.sleep(backoff_seconds * (2 ** (attempt - 1)))

    log.warning("FETCH_FAILED url=%s attempts=%d error=%s", url, last.attempts, last.error)
    return last


def build_client(timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> httpx.AsyncClient:
    """One client for a whole run, so connections are pooled across feeds."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    )
