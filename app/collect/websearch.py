"""Web search collector (optional, PRD 55).

Implemented against Brave's News Search API. Swapping to Serper or Tavily means
changing `_url` and `_parse` only -- nothing above this class knows the provider.

Disabled when `SEARCH_API_KEY` is unset.
"""

import datetime as dt
import json
import logging
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import httpx

from app.collect.base import CollectionResult, Collector, SourceOutcome
from app.collect.http import fetch
from app.collect.normalize import RawItem, to_article
from app.collect.queries import ENGLISH_NICHE_QUERIES, TELUGU_NICHE_QUERIES
from app.logging_setup import LOGGER_NAME
from app.models import Section, SourceType

BASE_URL = "https://api.search.brave.com/res/v1/news/search"
DOMAIN = "search.brave.com"
RESULT_COUNT = 20

# Search is aimed at the niche, where fixed feeds are thinnest. The global engine has
# 23 editorial feeds already; adding search there mostly adds low-reliability noise.
NICHE_SEARCH_QUERIES = ENGLISH_NICHE_QUERIES[:8] + TELUGU_NICHE_QUERIES


class WebSearchCollector(Collector):
    name = "websearch"

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
        if not self.enabled or section is Section.GLOBAL:
            return CollectionResult()

        result = CollectionResult()
        headers = {"X-Subscription-Token": self._api_key or "", "Accept": "application/json"}

        for query in NICHE_SEARCH_QUERIES:
            response = await fetch(client, self._url(query), headers=headers, logger=self._log)
            name = f"Brave Search: {query}"
            if not response.ok:
                result.outcomes.append(
                    SourceOutcome(DOMAIN, name, SourceType.SEARCH, False, error=response.error)
                )
                continue

            articles = self._parse(response.text, collected_at, tz, query)
            result.articles.extend(articles)
            result.outcomes.append(
                SourceOutcome(DOMAIN, name, SourceType.SEARCH, True, article_count=len(articles))
            )
        return result

    def _url(self, query: str) -> str:
        return f"{BASE_URL}?q={quote_plus(query)}&count={RESULT_COUNT}"

    def _parse(self, body: str, collected_at: dt.datetime, tz: ZoneInfo, query: str) -> list:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._log.warning("SEARCH_INVALID_JSON query=%s", query)
            return []

        articles = []
        for item in payload.get("results", []) or []:
            raw = RawItem(
                title=item.get("title") or "",
                url=item.get("url") or "",
                source_name=(item.get("meta_url") or {}).get("netloc") or "Web search",
                published_at=item.get("age") or item.get("page_age"),
                summary=item.get("description") or "",
            )
            article = to_article(raw, collected_at, tz, category="niche search")
            if article is not None:
                articles.append(article)
        return articles
