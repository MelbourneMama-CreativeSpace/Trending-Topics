"""Phase 3: RSS collection resilience and the source registry (PRD 16, 17, 58, 59)."""

import datetime as dt
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from app.collect.base import SourceOutcome
from app.collect.feeds import GLOBAL_FEEDS, NICHE_FEEDS, FeedSpec, niche_search_feeds
from app.collect.http import build_client
from app.collect.rss import RssCollector
from app.collect.service import CollectionService
from app.collect.sources import effective_reliability, reliability_index, update_registry
from app.collect.urls import is_valid_url
from app.errors import BriefingError, ErrorCode
from app.models import Section, Source, SourceType
from app.storage import Dataset

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 9, 1, 7, 30, tzinfo=IST)

FEED_A = FeedSpec("https://a.com/rss", "Source A", SourceType.NEWS, "world", 0.95)
FEED_B = FeedSpec("https://b.com/rss", "Source B", SourceType.NEWS, "world", 0.90)


def rss(*titles: str) -> bytes:
    items = "".join(
        f"<item><title>{title}</title><link>https://a.com/{index}</link>"
        f"<pubDate>Mon, 01 Sep 2026 01:00:00 GMT</pubDate></item>"
        for index, title in enumerate(titles)
    )
    body = f'<rss version="2.0"><channel><title>F</title>{items}</channel></rss>'
    return body.encode("utf-8")


def collector(*feeds):
    return RssCollector({Section.GLOBAL: feeds}, backoff_seconds=0)


# --- registry separation (PRD 17) -------------------------------------------


@pytest.mark.unit
def test_global_registry_carries_no_niche_bias():
    """PRD 17: the global engine must not receive the founder profile.

    If a Tollywood story leads Global Pulse it must be because it is genuinely large
    in the general news cycle, not because we went looking for it.
    """
    niche_terms = {"telugu", "tollywood", "film", "cinema", "podcast", "melbourne", "creator"}

    for feed in GLOBAL_FEEDS:
        haystack = f"{feed.name} {feed.category} {feed.url}".lower()
        assert not niche_terms & set(haystack.split()), f"niche bias in {feed.name}"


@pytest.mark.unit
def test_niche_search_covers_both_languages():
    """PRD 21 requires English and Telugu queries."""
    feeds = niche_search_feeds()

    assert any(feed.language == "te" for feed in feeds)
    assert any(feed.language == "en" for feed in feeds)


@pytest.mark.unit
def test_all_registered_feed_urls_are_valid():
    for feed in (*GLOBAL_FEEDS, *NICHE_FEEDS, *niche_search_feeds()):
        assert is_valid_url(feed.url), feed.url


# --- collection resilience (PRD 58) -----------------------------------------


@pytest.mark.integration
@respx.mock
async def test_articles_are_parsed_from_a_feed():
    respx.get(FEED_A.url).mock(return_value=httpx.Response(200, content=rss("First", "Second")))

    async with build_client() as client:
        result = await collector(FEED_A).collect(client, Section.GLOBAL, NOW, IST)

    assert [article.title for article in result.articles] == ["First", "Second"]
    assert result.succeeded == 1


@pytest.mark.integration
@respx.mock
async def test_one_failing_feed_does_not_stop_the_others():
    """PRD 58: never let one provider failure terminate the pipeline."""
    respx.get(FEED_A.url).mock(return_value=httpx.Response(500))
    respx.get(FEED_B.url).mock(return_value=httpx.Response(200, content=rss("Survivor")))

    async with build_client() as client:
        result = await collector(FEED_A, FEED_B).collect(client, Section.GLOBAL, NOW, IST)

    assert [article.title for article in result.articles] == ["Survivor"]
    assert result.succeeded == 1
    assert result.failed == 1


@pytest.mark.integration
@respx.mock
async def test_malformed_feed_body_is_survivable():
    respx.get(FEED_A.url).mock(return_value=httpx.Response(200, content=b"\x00not xml at all"))
    respx.get(FEED_B.url).mock(return_value=httpx.Response(200, content=rss("Survivor")))

    async with build_client() as client:
        result = await collector(FEED_A, FEED_B).collect(client, Section.GLOBAL, NOW, IST)

    assert [article.title for article in result.articles] == ["Survivor"]


@pytest.mark.integration
@respx.mock
async def test_entries_with_unusable_links_are_skipped_not_fatal():
    body = (
        b'<rss version="2.0"><channel>'
        b"<item><title>Bad</title><link>javascript:alert(1)</link></item>"
        b"<item><title>Good</title><link>https://a.com/ok</link></item>"
        b"</channel></rss>"
    )
    respx.get(FEED_A.url).mock(return_value=httpx.Response(200, content=body))

    async with build_client() as client:
        result = await collector(FEED_A).collect(client, Section.GLOBAL, NOW, IST)

    assert [article.title for article in result.articles] == ["Good"]


@pytest.mark.integration
@respx.mock
async def test_collection_never_raises_when_every_feed_fails():
    respx.get(FEED_A.url).mock(return_value=httpx.Response(503))
    respx.get(FEED_B.url).mock(side_effect=httpx.ConnectError("refused"))

    async with build_client() as client:
        result = await collector(FEED_A, FEED_B).collect(client, Section.GLOBAL, NOW, IST)

    assert result.articles == []
    assert result.failed == 2


# --- service-level total failure (PRD 59) -----------------------------------


@pytest.mark.integration
@respx.mock
async def test_total_failure_raises_no_usable_news():
    """PRD 59: RSS, news API and search all dead means no newsletter at all."""
    respx.get(FEED_A.url).mock(return_value=httpx.Response(503))
    service = CollectionService([RssCollector({Section.GLOBAL: (FEED_A,)}, backoff_seconds=0)])

    async with build_client() as client:
        with pytest.raises(BriefingError) as caught:
            await service.collect_all(client, NOW, IST)

    assert caught.value.code == ErrorCode.NO_USABLE_NEWS


@pytest.mark.integration
@respx.mock
async def test_partial_success_does_not_raise():
    """One working section is enough to continue; thresholds are Phase 8 territory."""
    respx.get(FEED_A.url).mock(return_value=httpx.Response(200, content=rss("Global story")))
    service = CollectionService([RssCollector({Section.GLOBAL: (FEED_A,)}, backoff_seconds=0)])

    async with build_client() as client:
        results = await service.collect_all(client, NOW, IST)

    assert len(results[Section.GLOBAL].articles) == 1
    assert results[Section.NICHE].articles == []


@pytest.mark.integration
@respx.mock
async def test_a_collector_that_raises_does_not_stop_the_rest():
    class Exploding(RssCollector):
        name = "exploding"

        async def collect(self, *args, **kwargs):
            raise RuntimeError("collector bug")

    respx.get(FEED_A.url).mock(return_value=httpx.Response(200, content=rss("Survivor")))
    service = CollectionService(
        [
            Exploding({}, backoff_seconds=0),
            RssCollector({Section.GLOBAL: (FEED_A,)}, backoff_seconds=0),
        ]
    )

    async with build_client() as client:
        results = await service.collect_all(client, NOW, IST)

    assert len(results[Section.GLOBAL].articles) == 1


# --- source reliability registry (PRD 16) -----------------------------------


@pytest.mark.unit
def test_registry_records_success_and_failure(repo):
    outcomes = [
        SourceOutcome("a.com", "Source A", SourceType.NEWS, True, article_count=5),
        SourceOutcome("b.com", "Source B", SourceType.NEWS, False, error="HTTP 500"),
    ]

    update_registry(repo, outcomes, NOW)

    stored = {source.source_domain: source for source in repo.read(Dataset.SOURCES)}
    assert stored["a.com"].last_success == NOW
    assert stored["a.com"].failure_count == 0
    assert stored["b.com"].last_failure == NOW
    assert stored["b.com"].failure_count == 1


@pytest.mark.unit
def test_consecutive_failures_accumulate(repo):
    failing = [SourceOutcome("b.com", "Source B", SourceType.NEWS, False, error="HTTP 500")]

    for _ in range(3):
        update_registry(repo, failing, NOW)

    assert repo.read(Dataset.SOURCES)[0].failure_count == 3


@pytest.mark.unit
def test_a_success_clears_the_failure_streak(repo):
    """A feed down yesterday and healthy today must not stay penalised."""
    update_registry(repo, [SourceOutcome("b.com", "B", SourceType.NEWS, False)], NOW)
    update_registry(repo, [SourceOutcome("b.com", "B", SourceType.NEWS, True)], NOW)

    assert repo.read(Dataset.SOURCES)[0].failure_count == 0


@pytest.mark.unit
def test_registry_survives_across_runs(repo):
    update_registry(repo, [SourceOutcome("a.com", "A", SourceType.NEWS, True)], NOW)
    update_registry(repo, [SourceOutcome("b.com", "B", SourceType.NEWS, True)], NOW)

    assert {source.source_domain for source in repo.read(Dataset.SOURCES)} == {"a.com", "b.com"}


@pytest.mark.unit
def test_healthy_source_keeps_its_full_reliability():
    source = Source(
        source_domain="reuters.com",
        source_name="Reuters",
        source_type=SourceType.NEWS,
        reliability_score=0.95,
        failure_count=0,
    )

    assert effective_reliability(source) == 0.95


@pytest.mark.unit
def test_flapping_source_loses_weight_without_editing_the_table():
    flapping = Source(
        source_domain="a.com",
        source_name="A",
        source_type=SourceType.NEWS,
        reliability_score=0.95,
        failure_count=3,
    )

    assert effective_reliability(flapping) < 0.95


@pytest.mark.unit
def test_persistently_dead_source_reaches_zero_weight():
    dead = Source(
        source_domain="a.com",
        source_name="A",
        source_type=SourceType.NEWS,
        reliability_score=0.95,
        failure_count=10,
    )

    assert effective_reliability(dead) == 0.0


@pytest.mark.unit
def test_reliability_index_ranks_wires_above_content_farms():
    """The founder's source-quality tier: high reliability gets strong weight."""
    sources = [
        Source(
            source_domain="wire.com",
            source_name="Wire",
            source_type=SourceType.NEWS,
            reliability_score=0.95,
        ),
        Source(
            source_domain="farm.com",
            source_name="Farm",
            source_type=SourceType.SEARCH,
            reliability_score=0.40,
        ),
    ]

    index = reliability_index(sources)

    assert index["wire.com"] > index["farm.com"]
