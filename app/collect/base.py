"""Collector interface and results (PRD 58)."""

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import httpx

from app.models import Article, Section, SourceType


@dataclass
class SourceOutcome:
    """Whether one source produced anything, for the sources.csv registry (PRD 16)."""

    source_domain: str
    source_name: str
    source_type: SourceType
    ok: bool
    article_count: int = 0
    error: str = ""


@dataclass
class CollectionResult:
    articles: list[Article] = field(default_factory=list)
    outcomes: list[SourceOutcome] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.ok)

    @property
    def failed(self) -> int:
        return sum(1 for outcome in self.outcomes if not outcome.ok)

    def merge(self, other: "CollectionResult") -> "CollectionResult":
        self.articles.extend(other.articles)
        self.outcomes.extend(other.outcomes)
        return self


class Collector(ABC):
    """One discovery channel: RSS, a news API, or web search.

    Implementations must never raise for an individual source failure -- they record a
    failed `SourceOutcome` and carry on, so one dead feed cannot end the run.
    """

    name: str = "collector"

    @abstractmethod
    async def collect(
        self,
        client: httpx.AsyncClient,
        section: Section,
        collected_at: dt.datetime,
        tz: ZoneInfo,
    ) -> CollectionResult: ...
