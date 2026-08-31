"""CSV row schemas (PRD 12-16).

Column names and order come straight from the PRD. `datetime` is imported as `dt` so
`TrendScore.date` can be a field name without shadowing the type.

Everything is serialised with `model_dump(mode="json")`, so datetimes become ISO 8601
strings with their offset intact -- a naive timestamp in this system is a bug, because
every retention and idempotency decision is made in IST.
"""

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field


class Section(StrEnum):
    """Which engine produced a topic (PRD 13). The two never mix."""

    GLOBAL = "global"
    NICHE = "niche"


class BriefingStatus(StrEnum):
    """Lifecycle of one day's run (PRD 15)."""

    STARTED = "started"
    PARTIAL = "partial"
    COMPLETED = "completed"
    FAILED = "failed"


class EmailStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"  # dry_run: rendered but deliberately not sent


class SourceType(StrEnum):
    """Source categories (PRD 16), used alongside reliability when weighting breadth."""

    OFFICIAL = "official"
    NEWS = "news"
    RSS = "rss"
    SEARCH = "search"
    INDUSTRY = "industry"
    SOCIAL = "social"


class Article(BaseModel):
    """One collected article (PRD 12)."""

    id: str
    title: str
    url: str
    source: str
    source_domain: str
    published_at: dt.datetime | None = None
    collected_at: dt.datetime
    language: str = "en"
    category: str = ""
    content_hash: str
    topic_id: str | None = None


class Topic(BaseModel):
    """A cluster of articles about one underlying story (PRD 13)."""

    topic_id: str
    headline: str
    description: str = ""
    section: Section
    first_seen: dt.datetime
    last_seen: dt.datetime
    created_at: dt.datetime


class TrendScore(BaseModel):
    """One day's score for one topic (PRD 14).

    `relevance_score` is populated for niche ranking only; global rows carry 0.
    """

    topic_id: str
    date: dt.date
    section: Section
    trend_score: float = Field(ge=0, le=100)
    recency_score: float = Field(default=0.0, ge=0, le=100)
    source_breadth_score: float = Field(default=0.0, ge=0, le=100)
    velocity_score: float = Field(default=0.0, ge=0, le=100)
    engagement_score: float = Field(default=0.0, ge=0, le=100)
    significance_score: float = Field(default=0.0, ge=0, le=100)
    relevance_score: float = Field(default=0.0, ge=0, le=100)


class Briefing(BaseModel):
    """One day's briefing run (PRD 15). The idempotency record."""

    briefing_id: str
    briefing_date: dt.date
    timezone: str
    status: BriefingStatus
    started_at: dt.datetime
    completed_at: dt.datetime | None = None
    global_count: int = 0
    niche_count: int = 0
    email_status: EmailStatus = EmailStatus.PENDING
    email_message_id: str | None = None
    error_code: str | None = None

    def was_delivered(self) -> bool:
        """True when today's work is genuinely done and must not be repeated.

        A partial briefing counts: PRD 62 says four verified niche stories are a valid
        send, so re-running would deliver a second email for the same day.
        """
        return (
            self.status in (BriefingStatus.COMPLETED, BriefingStatus.PARTIAL)
            and self.email_status == EmailStatus.SENT
        )


class Source(BaseModel):
    """Per-domain reliability registry (PRD 16).

    Feeds the source-quality weighting in Phase 5: breadth counts reliability, so a
    story carried by two wire services outranks one carried by ten content farms.
    """

    source_domain: str
    source_name: str
    source_type: SourceType
    reliability_score: float = Field(default=0.5, ge=0, le=1)
    last_success: dt.datetime | None = None
    last_failure: dt.datetime | None = None
    failure_count: int = 0


def build_briefing_id(day: dt.date, timezone: str) -> str:
    """PRD 48: the briefing key is date + timezone, e.g. 2026-09-01-Asia/Kolkata.

    The timezone is part of the key on purpose -- it makes a zone change visible as a
    different briefing rather than silently colliding with yesterday's record.
    """
    return f"{day.isoformat()}-{timezone}"
