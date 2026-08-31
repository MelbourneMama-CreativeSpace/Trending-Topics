"""Niche search queries (PRD 20, 21).

Google News exposes keyword search as an RSS feed, so these run with no API key.
The Telugu queries use the Telugu-language edition, which surfaces outlets the
English edition never returns.
"""

from urllib.parse import quote_plus

# PRD 21, English.
ENGLISH_NICHE_QUERIES: tuple[str, ...] = (
    "Telugu cinema",
    "Tollywood",
    "Telugu film industry",
    "Telugu OTT release",
    "Telugu short film",
    "Telugu podcast",
    "Telugu web series",
    "Telugu director",
    "AI filmmaking",
    "creator economy",
    "virtual production filmmaking",
    "short film festival",
    "podcast industry news",
    "Indian cinema Australia",
    "Melbourne film industry",
    "Telugu community Australia",
)

# PRD 21, Telugu.
TELUGU_NICHE_QUERIES: tuple[str, ...] = (
    "తెలుగు సినిమా",
    "తెలుగు సినిమా వార్తలు",
    "టాలీవుడ్",
    "తెలుగు దర్శకులు",
    "తెలుగు నటులు",
    "తెలుగు షార్ట్ ఫిల్మ్స్",
    "తెలుగు పాడ్‌కాస్ట్",
    "తెలుగు సినిమా ఆస్ట్రేలియా",
)

_GOOGLE_NEWS_SEARCH = "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"


def google_news_search_url(query: str, language: str = "en") -> str:
    """Build a Google News RSS search URL.

    `quote_plus` percent-encodes the Telugu script as UTF-8, which is what Google
    News expects -- an unencoded query silently returns nothing.
    """
    if language == "te":
        return _GOOGLE_NEWS_SEARCH.format(query=quote_plus(query), hl="te", gl="IN", ceid="IN:te")
    return _GOOGLE_NEWS_SEARCH.format(query=quote_plus(query), hl="en-IN", gl="IN", ceid="IN:en")
