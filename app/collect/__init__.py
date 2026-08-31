"""Source discovery (PRD 17-21, 58, 59)."""

from app.collect.base import CollectionResult, Collector, SourceOutcome
from app.collect.feeds import GLOBAL_FEEDS, NICHE_FEEDS, FeedSpec, niche_search_feeds
from app.collect.http import build_client, fetch
from app.collect.newsapi import NewsApiCollector
from app.collect.normalize import RawItem, content_hash, normalize_title, to_article
from app.collect.rss import RssCollector
from app.collect.service import CollectionService
from app.collect.sources import effective_reliability, reliability_index, update_registry
from app.collect.urls import canonical_url, domain_of, is_valid_url
from app.collect.websearch import WebSearchCollector

__all__ = [
    "GLOBAL_FEEDS",
    "NICHE_FEEDS",
    "CollectionResult",
    "CollectionService",
    "Collector",
    "FeedSpec",
    "NewsApiCollector",
    "RawItem",
    "RssCollector",
    "SourceOutcome",
    "WebSearchCollector",
    "build_client",
    "canonical_url",
    "content_hash",
    "domain_of",
    "effective_reliability",
    "fetch",
    "is_valid_url",
    "niche_search_feeds",
    "normalize_title",
    "reliability_index",
    "to_article",
    "update_registry",
]
