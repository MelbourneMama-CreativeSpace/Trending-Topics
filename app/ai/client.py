"""OpenRouter client (PRD 28, 29, 64).

The retry policy is imported from `app.collect.http` rather than restated, so the
system has one definition of what counts as transient. A 429 is worth waiting for; a
401 means the key is wrong and retrying it just burns the run's time budget.

The model is read from configuration and never hard-coded (PRD 28).
"""

import asyncio
import json
import logging
import re

import httpx

from app.collect.http import NON_RETRYABLE_STATUS, RETRYABLE_STATUS
from app.errors import BriefingError, ErrorCode, Severity
from app.logging_setup import LOGGER_NAME

DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_MAX_TOKENS = 1200
DEFAULT_TEMPERATURE = 0.3

# How much of a provider error body to log. Enough to identify the cause, short enough
# not to flood the run log.
ERROR_BODY_CHARS = 300

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def parse_json_object(text: str) -> dict:
    """Parse a JSON object from a model response, tolerating common wrappers.

    Models wrap JSON in markdown fences or add a sentence of preamble even when told
    not to. Being strict here would mark topics failed for a formatting habit rather
    than a content problem, so the outer braces are located explicitly.
    """
    cleaned = _FENCE_RE.sub("", text).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise BriefingError(
                ErrorCode.AI_INVALID_RESPONSE, "no JSON object in response"
            ) from None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise BriefingError(
                ErrorCode.AI_INVALID_RESPONSE, f"malformed JSON: {exc}"
            ) from exc

    if not isinstance(parsed, dict):
        raise BriefingError(ErrorCode.AI_INVALID_RESPONSE, "response was not an object")
    return parsed


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        attempts: int = DEFAULT_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._attempts = attempts
        self._backoff = backoff_seconds
        self._log = logger or logging.getLogger(LOGGER_NAME)

    async def complete_json(
        self,
        client: httpx.AsyncClient,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> dict:
        """One chat completion, parsed as a JSON object.

        Raises `BriefingError` with an AI_* code on failure. Callers treat that as one
        topic failing, never as the run failing (PRD 30).
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": DEFAULT_TEMPERATURE,
        }
        # Deliberately no `response_format: json_object`. Kimi rejects it with
        # "does not support feature: structured-outputs", and PRD 28 requires the
        # model to stay swappable -- so support cannot be assumed for whatever model
        # is configured next. JSON is enforced by the prompt and by the defensive
        # parsing in `parse_json_object`, which works with every model.
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        last_error = "no attempt made"

        for attempt in range(1, self._attempts + 1):
            try:
                response = await client.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=headers
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt == self._attempts:
                    raise BriefingError(
                        ErrorCode.AI_TIMEOUT, last_error, severity=Severity.ERROR
                    ) from exc
            else:
                if response.status_code == 200:
                    return parse_json_object(_extract_content(response.json()))

                last_error = f"HTTP {response.status_code}"
                if response.status_code in NON_RETRYABLE_STATUS:
                    # A bad key or a bad request will fail identically next time.
                    # The body is logged because the status alone is not diagnosable:
                    # a 400 here once meant an unsupported parameter, and without the
                    # body that took a separate script to discover.
                    self._log.error(
                        "AI_REQUEST_REJECTED status=%d body=%s",
                        response.status_code,
                        " ".join(response.text[:ERROR_BODY_CHARS].split()),
                    )
                    raise BriefingError(
                        ErrorCode.AI_INVALID_RESPONSE, last_error, severity=Severity.ERROR
                    )
                if response.status_code not in RETRYABLE_STATUS:
                    raise BriefingError(
                        ErrorCode.AI_INVALID_RESPONSE, last_error, severity=Severity.ERROR
                    )
                if attempt == self._attempts:
                    code = (
                        ErrorCode.AI_RATE_LIMITED
                        if response.status_code == 429
                        else ErrorCode.AI_TIMEOUT
                    )
                    raise BriefingError(code, last_error, severity=Severity.ERROR)

            await asyncio.sleep(self._backoff * (2 ** (attempt - 1)))

        raise BriefingError(
            ErrorCode.AI_INVALID_RESPONSE, last_error, severity=Severity.ERROR
        )


def _extract_content(body: dict) -> str:
    try:
        return body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise BriefingError(
            ErrorCode.AI_INVALID_RESPONSE, "response had no message content"
        ) from exc


def build_ai_client(timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))
