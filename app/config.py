"""Application configuration (PRD 55).

Secrets are held as `SecretStr` so they cannot leak through reprs, tracebacks, or
accidental logging -- pydantic renders them as `**********`. Call
`.get_secret_value()` only at the point of use.

Two tiers of config:

* **Startup-required** -- `AGENT_SECRET`. The endpoint is unauthenticated without it,
  so the app refuses to boot. This is the "fail loudly" boundary.
* **Pipeline-required** -- OpenRouter and email credentials. These are checked by
  `missing_pipeline_config()` before a briefing actually runs, so the API can boot
  and serve `/health` on a machine that has no keys yet.
"""

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Amendment A1: IST, superseding the PRD's Australia/Melbourne. See PLAN.md.
DEFAULT_TIMEZONE = "Asia/Kolkata"

# An AGENT_SECRET shorter than this is almost certainly a placeholder.
MIN_AGENT_SECRET_LENGTH = 16


class Settings(BaseSettings):
    """Environment-backed settings. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Endpoint auth (PRD 50) -- required at startup ---
    agent_secret: SecretStr

    # --- AI gateway (PRD 28). Model is never hard-coded in business logic. ---
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str = "moonshotai/kimi-k2"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # --- Email: Resend (PRD 46) ---
    email_api_key: SecretStr | None = None
    sender_email: str | None = None
    recipient_email: str | None = None

    # --- Behaviour (PRD 55) ---
    timezone: str = DEFAULT_TIMEZONE
    global_top_n: int = Field(default=5, ge=1, le=20)
    niche_top_n: int = Field(default=5, ge=1, le=20)
    data_retention_days: int = Field(default=31, ge=1, le=365)

    # --- Optional source providers. RSS needs none of these. ---
    news_api_key: SecretStr | None = None
    search_api_key: SecretStr | None = None

    # --- Git-backed CSV persistence (PRD 8) ---
    github_token: SecretStr | None = None
    github_data_repo: str = "MelbourneMama-CreativeSpace/Trending-Topics"
    github_data_branch: str = "main"

    @field_validator("agent_secret")
    @classmethod
    def _agent_secret_is_strong_enough(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < MIN_AGENT_SECRET_LENGTH:
            raise ValueError(f"AGENT_SECRET must be at least {MIN_AGENT_SECRET_LENGTH} characters")
        return v

    @field_validator("timezone")
    @classmethod
    def _timezone_must_resolve(cls, v: str) -> str:
        """Catch a bad zone at startup rather than mid-run.

        Also catches a missing `tzdata` package, which is how this fails on Windows
        and on slim Linux images.
        """
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"TIMEZONE={v!r} could not be resolved. Check the name and that "
                f"`tzdata` is installed."
            ) from exc
        return v

    @property
    def tz(self) -> ZoneInfo:
        """The briefing timezone. Every date boundary in this system uses it."""
        return ZoneInfo(self.timezone)

    def missing_pipeline_config(self) -> list[str]:
        """Env vars needed to run a briefing but not to boot the API.

        Returns the names of whatever is absent, so the caller can report all of
        them at once instead of one per attempt.
        """
        required = {
            "OPENROUTER_API_KEY": self.openrouter_api_key,
            "EMAIL_API_KEY": self.email_api_key,
            "SENDER_EMAIL": self.sender_email,
            "RECIPIENT_EMAIL": self.recipient_email,
        }
        return sorted(name for name, value in required.items() if not value)

    def secret_values(self) -> list[str]:
        """Plaintext of every secret, for the log redaction filter.

        This is the one place secrets are unwrapped in bulk. The result is handed
        straight to `SecretRedactionFilter` and never logged or returned.
        """
        candidates = (
            self.agent_secret,
            self.openrouter_api_key,
            self.email_api_key,
            self.news_api_key,
            self.search_api_key,
            self.github_token,
        )
        return [s.get_secret_value() for s in candidates if s is not None]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings, so the env is read once per process.

    Tests call `get_settings.cache_clear()` after changing the environment.
    """
    return Settings()
