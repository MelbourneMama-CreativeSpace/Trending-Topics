"""Output validation and source integrity (PRD 34, 35, 67, 68).

The rule this module exists to enforce: **no URL reaches the email unless it was
actually retrieved**. It is defended twice.

* The schema never asks the model for a URL. Sources are cited by index and the final
  objects are rebuilt from collected articles, so a fabricated reference has no field
  to live in.
* Any URL-shaped text the model writes into a prose field is stripped unless it
  matches a retrieved URL exactly. This catches the case where a model helpfully
  inlines "see https://example.com/story" into its summary.
"""

import logging
import re

from app.ai.schemas import BriefSource, TopicBrief
from app.cluster.clusterer import Cluster
from app.collect.urls import canonical_url, is_valid_url
from app.logging_setup import LOGGER_NAME

MAX_SOURCES_PER_TOPIC = 5

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_REDACTION = "[link removed]"

_log = logging.getLogger(LOGGER_NAME)


def retrieved_sources(cluster: Cluster) -> list[BriefSource]:
    """Citable sources for a topic, built entirely from collected articles.

    Deduplicated by domain so one topic does not cite the same outlet five times, and
    URL-validated again here even though collection validated them: this is the last
    gate before the email (PRD 68).
    """
    sources: list[BriefSource] = []
    seen: set[str] = set()

    for article in cluster.articles:
        if article.source_domain in seen or not is_valid_url(article.url):
            continue
        seen.add(article.source_domain)
        sources.append(
            BriefSource(
                title=article.title,
                url=article.url,
                publisher=article.source or article.source_domain,
            )
        )
        if len(sources) >= MAX_SOURCES_PER_TOPIC:
            break

    return sources


def select_sources(
    available: list[BriefSource], indices: list[int]
) -> list[BriefSource]:
    """Map the model's 1-based citations onto real sources.

    Out-of-range indices are dropped rather than raising: a model citing source 9 of 4
    is a hallucinated citation, and the correct response is to not cite it. If nothing
    valid survives, every retrieved source is used, because a story with no citation
    is worse than one over-cited (PRD 40).
    """
    chosen = [
        available[index - 1]
        for index in dict.fromkeys(indices)
        if 1 <= index <= len(available)
    ]
    dropped = len(set(indices)) - len(chosen)
    if dropped > 0:
        _log.warning("AI_CITED_UNKNOWN_SOURCE dropped=%d available=%d", dropped, len(available))

    return chosen or available


def scrub_fabricated_urls(text: str, allowed: set[str]) -> str:
    """Remove URLs the model wrote that were never retrieved (PRD 35)."""
    def replace(match: re.Match[str]) -> str:
        candidate = match.group(0).rstrip(".,;:")
        if is_valid_url(candidate) and canonical_url(candidate) in allowed:
            return match.group(0)
        _log.warning("AI_FABRICATED_URL_STRIPPED")
        return _REDACTION

    return _URL_RE.sub(replace, text)


def scrub_brief(brief: TopicBrief, allowed_urls: set[str]) -> TopicBrief:
    """Strip fabricated URLs from every free-text field of a brief."""
    allowed = {canonical_url(url) for url in allowed_urls}

    return brief.model_copy(
        update={
            "headline": scrub_fabricated_urls(brief.headline, allowed),
            "what_happened": scrub_fabricated_urls(brief.what_happened, allowed),
            "why_trending": scrub_fabricated_urls(brief.why_trending, allowed),
            "why_it_matters": scrub_fabricated_urls(brief.why_it_matters, allowed),
            "creative_angle": (
                scrub_fabricated_urls(brief.creative_angle, allowed)
                if brief.creative_angle
                else None
            ),
            "key_facts": [scrub_fabricated_urls(f, allowed) for f in brief.key_facts],
            "uncertainties": [
                scrub_fabricated_urls(u, allowed) for u in brief.uncertainties
            ],
        }
    )
