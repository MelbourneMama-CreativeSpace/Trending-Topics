"""Turn a raw feed or API item into a validated Article (PRD 12).

Everything here treats its input as untrusted: titles come from third-party feeds and
may contain HTML, control characters, or prompt-injection text. Nothing is executed and
nothing is rendered -- text is flattened, and the URL must pass validation or the item
is dropped.
"""

import datetime as dt
import hashlib
import html
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

from app.collect.urls import canonical_url, domain_of, is_valid_url
from app.models import Article

MAX_TITLE_LENGTH = 300

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Google News RSS appends " - Publisher" to every headline.
_PUBLISHER_SUFFIX_RE = re.compile(r"\s+[-–—]\s+[^-–—]{2,40}$")

# Unicode categories: P* is punctuation, S* is symbols. Deliberately NOT `[^\w\s]`,
# which also strips category Mn (combining marks) -- those are Telugu vowel signs, so
# that pattern turns "తెలుగు" into "త ల గ" and destroys every Telugu headline.
_STRIPPED_CATEGORY_PREFIXES = ("P", "S")


@dataclass
class RawItem:
    """One item as it arrived, before validation."""

    title: str
    url: str
    source_name: str
    publisher_url: str = ""
    """Originating publisher, when the link itself is an aggregator redirect.

    Google News RSS links are opaque `news.google.com/rss/articles/CBMi...` redirects,
    so deriving the domain from the link collapses every search result onto one domain
    and destroys the source-breadth signal. The entry carries the real publisher
    separately; this is where it goes.
    """
    published_at: Any = None
    summary: str = ""
    language: str = "en"
    category: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def clean_text(value: Any) -> str:
    """Flatten third-party text: unescape entities, drop tags, collapse whitespace."""
    if not value:
        return ""
    text = html.unescape(str(value))
    text = _TAG_RE.sub(" ", text)
    text = _CONTROL_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def strip_publisher_suffix(title: str) -> str:
    """Remove the ' - Publisher' tail Google News adds.

    Left in place it defeats title-similarity dedup, because the same wire story
    carried by three outlets produces three different-looking headlines.
    """
    stripped = _PUBLISHER_SUFFIX_RE.sub("", title).strip()
    # Guard against eating a genuinely short headline that merely contains a dash.
    return stripped if len(stripped) >= 15 else title


def normalize_title(title: str) -> str:
    """Comparison form: casefolded, punctuation-free, whitespace-collapsed.

    Strips by Unicode category rather than by regex character class, so Telugu
    combining vowel signs survive. See `_STRIPPED_CATEGORY_PREFIXES`.
    """
    text = clean_text(title).casefold()
    stripped = "".join(
        " " if unicodedata.category(char).startswith(_STRIPPED_CATEGORY_PREFIXES) else char
        for char in text
    )
    return _WHITESPACE_RE.sub(" ", stripped).strip()


def content_hash(title: str) -> str:
    """Hash of the comparison form, so syndicated copies collide immediately."""
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()[:16]


def article_id(url: str) -> str:
    """Stable id derived from the canonical URL, so reruns produce the same id."""
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:16]


def parse_published(value: Any, tz: ZoneInfo) -> dt.datetime | None:
    """Parse the many shapes a publication date arrives in.

    feedparser hands back a UTC `struct_time`; APIs send ISO 8601; some feeds send
    RFC 822 strings. A naive result is assumed to be UTC rather than local, because
    that is what feeds overwhelmingly mean.
    """
    if value is None or value == "":
        return None

    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, time.struct_time):
        return dt.datetime(*value[:6], tzinfo=dt.UTC).astimezone(tz)
    else:
        try:
            parsed = date_parser.parse(str(value))
        except (ValueError, OverflowError, TypeError):
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(tz)


def to_article(
    raw: RawItem,
    collected_at: dt.datetime,
    tz: ZoneInfo,
    category: str = "",
) -> Article | None:
    """Build an Article, or return None if the item is unusable.

    Dropping an item is normal and expected: feeds carry placeholder entries, ads and
    malformed rows. One bad item must never cost us the rest of the feed.
    """
    if not is_valid_url(raw.url):
        return None

    title = strip_publisher_suffix(clean_text(raw.title))[:MAX_TITLE_LENGTH]
    if not title:
        return None

    url = canonical_url(raw.url)
    # Prefer the originating publisher over the link's host, so aggregator redirects
    # still contribute real breadth.
    domain = (
        domain_of(raw.publisher_url)
        if raw.publisher_url and is_valid_url(raw.publisher_url)
        else domain_of(url)
    )

    return Article(
        id=article_id(url),
        title=title,
        url=url,
        source=clean_text(raw.source_name) or domain,
        source_domain=domain,
        published_at=parse_published(raw.published_at, tz),
        collected_at=collected_at,
        language=raw.language or "en",
        category=category or raw.category or "",
        content_hash=content_hash(title),
    )
