"""RSS collection (PRD 58).

feedparser is given bytes we already fetched, so it never opens its own connection and
never bypasses the timeout and retry policy in `http.py`.

One dead feed produces one failed `SourceOutcome`. It never raises.
"""

import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

import feedparser
import httpx

from app.collect.base import CollectionResult, Collector, SourceOutcome
from app.collect.feeds import FeedSpec
from app.collect.http import fetch
from app.collect.normalize import RawItem, to_article
from app.collect.urls import domain_of
from app.logging_setup import LOGGER_NAME
from app.models import Article, Section

MAX_ENTRIES_PER_FEED = 25
DEFAULT_CONCURRENCY = 8


class RssCollector(Collector):
    name = "rss"

    def __init__(
        self,
        feeds_by_section: dict[Section, tuple[FeedSpec, ...]],
        concurrency: int = DEFAULT_CONCURRENCY,
        backoff_seconds: float = 1.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._feeds = feeds_by_section
        self._semaphore = asyncio.Semaphore(concurrency)
        self._backoff = backoff_seconds
        self._log = logger or logging.getLogger(LOGGER_NAME)

    async def collect(
        self,
        client: httpx.AsyncClient,
        section: Section,
        collected_at: dt.datetime,
        tz: ZoneInfo,
    ) -> CollectionResult:
        feeds = self._feeds.get(section, ())
        if not feeds:
            return CollectionResult()

        gathered = await asyncio.gather(
            *(self._one_feed(client, feed, collected_at, tz) for feed in feeds)
        )

        result = CollectionResult()
        for articles, outcome in gathered:
            result.articles.extend(articles)
            result.outcomes.append(outcome)
        return result

    async def _one_feed(
        self,
        client: httpx.AsyncClient,
        feed: FeedSpec,
        collected_at: dt.datetime,
        tz: ZoneInfo,
    ) -> tuple[list[Article], SourceOutcome]:
        async with self._semaphore:
            response = await fetch(
                client, feed.url, backoff_seconds=self._backoff, logger=self._log
            )

        domain = domain_of(feed.url)
        if not response.ok:
            return [], SourceOutcome(
                source_domain=domain,
                source_name=feed.name,
                source_type=feed.source_type,
                ok=False,
                error=response.error,
            )

        try:
            articles = self._parse(response.content, feed, collected_at, tz)
        except Exception as exc:
            # A malformed feed body must not take the run down with it.
            self._log.warning("FEED_PARSE_FAILED feed=%s error=%s", feed.name, exc)
            return [], SourceOutcome(
                source_domain=domain,
                source_name=feed.name,
                source_type=feed.source_type,
                ok=False,
                error=f"parse failed: {exc}",
            )

        return articles, SourceOutcome(
            source_domain=domain,
            source_name=feed.name,
            source_type=feed.source_type,
            ok=True,
            article_count=len(articles),
        )

    def _parse(
        self, body: bytes, feed: FeedSpec, collected_at: dt.datetime, tz: ZoneInfo
    ) -> list[Article]:
        parsed = feedparser.parse(body)
        articles: list[Article] = []

        for entry in parsed.entries[:MAX_ENTRIES_PER_FEED]:
            raw = _entry_to_raw(entry, feed)
            if raw is None:
                continue
            article = to_article(raw, collected_at, tz, category=feed.category)
            if article is not None:
                articles.append(article)

        return articles


def _entry_to_raw(entry: dict, feed: FeedSpec) -> RawItem | None:
    """Map one feed entry, accounting for the shapes different feeds use."""
    # Google Trends items carry the trending term in <title> and the actual article
    # under <ht:news_item_url>. Without this the whole Trends feed is dropped, because
    # its own <link> points at a Trends explore page, not a story.
    url = entry.get("ht_news_item_url") or entry.get("link") or ""
    title = entry.get("ht_news_item_title") or entry.get("title") or ""
    if not url or not title:
        return None

    # Google News nests the originating publisher as {"href": ..., "title": ...}.
    # Both matter: the title names the outlet, and the href is the only way to recover
    # a real domain from an opaque news.google.com redirect link.
    source_block = entry.get("source") or {}
    if not isinstance(source_block, dict):
        source_block = {}

    # Publisher name, in order of trustworthiness: Google News nests it in <source>,
    # Google Trends supplies <ht:news_item_source>, and only then do we fall back to
    # the feed name. Falling back too early attributes a Michigan Advance article to
    # "Google Trends (US)" -- a false attribution printed in the email.
    publisher_name = (
        source_block.get("title") or entry.get("ht_news_item_source") or feed.name
    )

    return RawItem(
        title=title,
        url=url,
        source_name=publisher_name,
        publisher_url=source_block.get("href") or "",
        published_at=(
            entry.get("published_parsed") or entry.get("updated_parsed") or entry.get("published")
        ),
        summary=entry.get("summary", ""),
        language=feed.language,
        category=feed.category,
    )
