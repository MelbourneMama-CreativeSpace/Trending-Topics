"""Feed registries and source reliability (PRD 17, 18, 20; founder's source-quality tier).

Two registries, deliberately separate. PRD 17 is explicit that the global engine must
not see the founder's interests, so `GLOBAL_FEEDS` contains no Telugu, filmmaking,
podcast or Melbourne source. If a Tollywood story leads the global section, it got
there by being genuinely large in the general news cycle -- not by being selected for.

`reliability` weights a source's contribution to a topic's breadth score in Phase 5:
two wire services must outrank ten content farms carrying the same syndicated copy.
"""

from dataclasses import dataclass

from app.collect.queries import (
    ENGLISH_NICHE_QUERIES,
    TELUGU_NICHE_QUERIES,
    google_news_search_url,
)
from app.models import SourceType

# Reliability bands. Judgement calls, revisable from observed behaviour in sources.csv.
WIRE = 0.95  # international desks with their own reporting
NATIONAL = 0.90  # major national mastheads
TRADE = 0.85  # specialist trade press, authoritative in its lane
TECH_PRESS = 0.80
AGGREGATOR = 0.60  # ranks other people's reporting; breadth signal, not a source
UNKNOWN = 0.40  # anything first seen via search

DEFAULT_RELIABILITY_BY_TYPE = {
    SourceType.OFFICIAL: 0.95,
    SourceType.NEWS: 0.75,
    SourceType.RSS: 0.70,
    SourceType.INDUSTRY: TRADE,
    SourceType.SEARCH: UNKNOWN,
    SourceType.SOCIAL: 0.35,
}


@dataclass(frozen=True)
class FeedSpec:
    url: str
    name: str
    source_type: SourceType
    category: str = ""
    reliability: float = UNKNOWN
    language: str = "en"


# --- Global registry (PRD 18). No niche bias. -------------------------------

GLOBAL_FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec(
        "https://feeds.bbci.co.uk/news/world/rss.xml", "BBC News", SourceType.NEWS, "world", WIRE
    ),
    FeedSpec(
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "BBC Business",
        SourceType.NEWS,
        "business",
        WIRE,
    ),
    FeedSpec(
        "https://feeds.bbci.co.uk/news/technology/rss.xml",
        "BBC Technology",
        SourceType.NEWS,
        "technology",
        WIRE,
    ),
    FeedSpec(
        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "BBC Science",
        SourceType.NEWS,
        "science",
        WIRE,
    ),
    FeedSpec(
        "https://www.theguardian.com/world/rss", "The Guardian", SourceType.NEWS, "world", WIRE
    ),
    FeedSpec(
        "https://www.theguardian.com/business/rss",
        "The Guardian Business",
        SourceType.NEWS,
        "business",
        WIRE,
    ),
    FeedSpec(
        "https://www.theguardian.com/science/rss",
        "The Guardian Science",
        SourceType.NEWS,
        "science",
        WIRE,
    ),
    FeedSpec("https://feeds.npr.org/1001/rss.xml", "NPR", SourceType.NEWS, "world", WIRE),
    FeedSpec(
        "https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera", SourceType.NEWS, "world", WIRE
    ),
    FeedSpec(
        "https://www.abc.net.au/news/feed/51120/rss.xml",
        "ABC News Australia",
        SourceType.NEWS,
        "australia",
        WIRE,
    ),
    FeedSpec(
        "https://www.thehindu.com/news/national/feeder/default.rss",
        "The Hindu",
        SourceType.NEWS,
        "india",
        NATIONAL,
    ),
    FeedSpec(
        "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
        "Times of India",
        SourceType.NEWS,
        "india",
        NATIONAL,
    ),
    FeedSpec(
        "https://techcrunch.com/feed/", "TechCrunch", SourceType.NEWS, "technology", TECH_PRESS
    ),
    FeedSpec(
        "https://feeds.arstechnica.com/arstechnica/index",
        "Ars Technica",
        SourceType.NEWS,
        "technology",
        TECH_PRESS,
    ),
    FeedSpec(
        "https://www.theverge.com/rss/index.xml",
        "The Verge",
        SourceType.NEWS,
        "technology",
        TECH_PRESS,
    ),
    FeedSpec(
        "https://hnrss.org/frontpage", "Hacker News", SourceType.SOCIAL, "internet", AGGREGATOR
    ),
    FeedSpec("https://www.espn.com/espn/rss/news", "ESPN", SourceType.NEWS, "sports", 0.75),
    FeedSpec(
        "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
        "Google News (US)",
        SourceType.SEARCH,
        "world",
        AGGREGATOR,
    ),
    FeedSpec(
        "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en",
        "Google News (India)",
        SourceType.SEARCH,
        "india",
        AGGREGATOR,
    ),
    FeedSpec(
        "https://news.google.com/rss?hl=en-AU&gl=AU&ceid=AU:en",
        "Google News (Australia)",
        SourceType.SEARCH,
        "australia",
        AGGREGATOR,
    ),
    # Google Trends daily search trends: a velocity signal no editorial feed provides.
    FeedSpec(
        "https://trends.google.com/trending/rss?geo=IN",
        "Google Trends (India)",
        SourceType.SEARCH,
        "trends",
        AGGREGATOR,
    ),
    FeedSpec(
        "https://trends.google.com/trending/rss?geo=US",
        "Google Trends (US)",
        SourceType.SEARCH,
        "trends",
        AGGREGATOR,
    ),
    FeedSpec(
        "https://trends.google.com/trending/rss?geo=AU",
        "Google Trends (Australia)",
        SourceType.SEARCH,
        "trends",
        AGGREGATOR,
    ),
)


# --- Niche registry (PRD 20) ------------------------------------------------

NICHE_FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec("https://variety.com/feed/", "Variety", SourceType.INDUSTRY, "entertainment", TRADE),
    FeedSpec("https://deadline.com/feed/", "Deadline", SourceType.INDUSTRY, "entertainment", TRADE),
    FeedSpec(
        "https://www.hollywoodreporter.com/feed/",
        "The Hollywood Reporter",
        SourceType.INDUSTRY,
        "entertainment",
        TRADE,
    ),
    FeedSpec(
        "https://www.indiewire.com/feed/", "IndieWire", SourceType.INDUSTRY, "filmmaking", TRADE
    ),
    FeedSpec(
        "https://nofilmschool.com/rss.xml",
        "No Film School",
        SourceType.INDUSTRY,
        "filmmaking",
        0.75,
    ),
    FeedSpec(
        "https://www.tubefilter.com/feed/",
        "Tubefilter",
        SourceType.INDUSTRY,
        "creator economy",
        0.75,
    ),
    FeedSpec("https://podnews.net/rss", "Podnews", SourceType.INDUSTRY, "podcasting", 0.75),
    FeedSpec(
        "https://www.thehindu.com/entertainment/movies/feeder/default.rss",
        "The Hindu Cinema",
        SourceType.NEWS,
        "telugu cinema",
        NATIONAL,
    ),
)


def reliability_for(source_type: SourceType) -> float:
    """Fallback reliability for a domain we have never seen before."""
    return DEFAULT_RELIABILITY_BY_TYPE.get(source_type, UNKNOWN)


def niche_search_feeds() -> tuple[FeedSpec, ...]:
    """Keyword search as RSS, so the niche engine works with zero API keys (PRD 21).

    Fixed feeds cover trade press well but barely touch Telugu-language reporting.
    These queries are how the Creative Radar reaches Telugu outlets at all.
    """
    english = tuple(
        FeedSpec(
            google_news_search_url(query, "en"),
            f"Google News: {query}",
            SourceType.SEARCH,
            "niche search",
            AGGREGATOR,
            "en",
        )
        for query in ENGLISH_NICHE_QUERIES
    )
    telugu = tuple(
        FeedSpec(
            google_news_search_url(query, "te"),
            f"Google News: {query}",
            SourceType.SEARCH,
            "niche search",
            AGGREGATOR,
            "te",
        )
        for query in TELUGU_NICHE_QUERIES
    )
    return english + telugu
