"""NewsAPI.org collector (optional, PRD 55).

Disabled when `NEWS_API_KEY` is unset, which is the current state. Note that NewsAPI's
free tier is development-only and rejects requests from deployed servers, so this may
succeed locally and return 426 on Render -- handled as an ordinary source failure.
"""

import datetime as dt
import json
import logging
from zoneinfo import ZoneInfo

import httpx

from app.collect.base import CollectionResult, Collector, SourceOutcome
from app.collect.feeds import AGGREGATOR
from app.collect.http import fetch
from app.collect.normalize import RawItem, to_article
from app.collect.queries import ENGLISH_NICHE_QUERIES
from app.logging_setup import LOGGER_NAME
from app.models import Section, SourceType

BASE_URL = "https://newsapi.org/v2"
PAGE_SIZE = 30
DOMAIN = "newsapi.org"

# PRD 18 categories that NewsAPI actually supports.
GLOBAL_CATEGORIES = ("general", "business", "technology", "science", "sports")

# A handful of broad queries; the RSS layer already covers the niche in depth.
NICHE_QUERIES = ENGLISH_NICHE_QUERIES[:6]


class NewsApiCollector(Collector):
    name = "newsapi"

    def __init__(self, api_key: str | None, logger: logging.Logger | None = None) -> None:
        self._api_key = api_key
        self._log = logger or logging.getLogger(LOGGER_NAME)

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def collect(
        self,
        client: httpx.AsyncClient,
        section: Section,
        collected_at: dt.datetime,
        tz: ZoneInfo,
    ) -> CollectionResult:
        if not self.enabled:
            return CollectionResult()

        result = CollectionResult()
        for url, label in self._requests(section):
            response = await fetch(
                client, url, headers={"X-Api-Key": self._api_key or ""}, logger=self._log
            )
            if not response.ok:
                result.outcomes.append(
                    SourceOutcome(
                        DOMAIN, f"NewsAPI ({label})", SourceType.NEWS, False, error=response.error
                    )
                )
                continue

            articles = self._parse(response.text, collected_at, tz, label)
            result.articles.extend(articles)
            result.outcomes.append(
                SourceOutcome(
                    DOMAIN, f"NewsAPI ({label})", SourceType.NEWS, True, article_count=len(articles)
                )
            )
        return result

    def _requests(self, section: Section) -> list[tuple[str, str]]:
        if section is Section.GLOBAL:
            return [
                (f"{BASE_URL}/top-headlines?language=en&pageSize={PAGE_SIZE}&category={c}", c)
                for c in GLOBAL_CATEGORIES
            ]
        return [
            (
                f"{BASE_URL}/everything?language=en&sortBy=publishedAt"
                f"&pageSize={PAGE_SIZE}&q={httpx.QueryParams({'q': q})['q']}",
                q,
            )
            for q in NICHE_QUERIES
        ]

    def _parse(self, body: str, collected_at: dt.datetime, tz: ZoneInfo, label: str) -> list:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._log.warning("NEWSAPI_INVALID_JSON label=%s", label)
            return []

        articles = []
        for item in payload.get("articles", []) or []:
            raw = RawItem(
                title=item.get("title") or "",
                url=item.get("url") or "",
                source_name=(item.get("source") or {}).get("name") or "NewsAPI",
                published_at=item.get("publishedAt"),
                summary=item.get("description") or "",
            )
            article = to_article(raw, collected_at, tz, category=label)
            if article is not None:
                articles.append(article)
        return articles


__all__ = ["AGGREGATOR", "NewsApiCollector"]
