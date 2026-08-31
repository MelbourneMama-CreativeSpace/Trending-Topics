"""Deduplication (PRD 24).

Three levels, cheapest first:

1. **Canonical URL** -- the same page reached by different links. Already largely
   handled in `collect.urls`, applied here as an exact grouping.
2. **Content hash** -- identical normalised headlines. This is syndicated wire copy
   appearing verbatim under many mastheads.
3. **Near-identical titles** -- headlines that differ only cosmetically.

Dedup answers "is this the same *article*". Clustering answers the different question
"is this the same *story*", and runs afterwards on what survives.
"""

from collections import defaultdict
from dataclasses import dataclass

from app.cluster.similarity import are_near_duplicate_titles
from app.cluster.text import token_set
from app.models import Article


@dataclass
class DedupReport:
    kept: list[Article]
    removed_by_url: int = 0
    removed_by_hash: int = 0
    removed_by_title: int = 0

    @property
    def total_removed(self) -> int:
        return self.removed_by_url + self.removed_by_hash + self.removed_by_title


def deduplicate(
    articles: list[Article], reliability: dict[str, float] | None = None
) -> DedupReport:
    """Collapse duplicate articles, keeping the best copy of each."""
    reliability = reliability or {}

    by_url: dict[str, Article] = {}
    removed_url = 0
    for article in articles:
        existing = by_url.get(article.url)
        if existing is None:
            by_url[article.url] = article
        else:
            by_url[article.url] = _better(existing, article, reliability)
            removed_url += 1

    by_hash: dict[str, Article] = {}
    removed_hash = 0
    for article in by_url.values():
        existing = by_hash.get(article.content_hash)
        if existing is None:
            by_hash[article.content_hash] = article
        else:
            by_hash[article.content_hash] = _better(existing, article, reliability)
            removed_hash += 1

    kept, removed_title = _collapse_near_duplicate_titles(
        list(by_hash.values()), reliability
    )

    return DedupReport(
        kept=kept,
        removed_by_url=removed_url,
        removed_by_hash=removed_hash,
        removed_by_title=removed_title,
    )


def _collapse_near_duplicate_titles(
    articles: list[Article], reliability: dict[str, float]
) -> tuple[list[Article], int]:
    """Level 3, with token blocking so this stays linear-ish rather than O(n^2).

    Only articles sharing at least one content token are ever compared. Two headlines
    with no word in common cannot be 92% similar, so nothing is missed.
    """
    index: dict[str, list[int]] = defaultdict(list)
    kept: list[Article] = []
    removed = 0

    for article in articles:
        tokens = token_set(article.title)
        candidates = {
            position for token in tokens for position in index.get(token, ())
        }

        match = next(
            (
                position
                for position in sorted(candidates)
                if are_near_duplicate_titles(article.title, kept[position].title)
            ),
            None,
        )

        if match is None:
            position = len(kept)
            kept.append(article)
            for token in tokens:
                index[token].append(position)
        else:
            kept[match] = _better(kept[match], article, reliability)
            removed += 1

    return kept, removed


def _better(left: Article, right: Article, reliability: dict[str, float]) -> Article:
    """Prefer the more reliable source, then the earlier report.

    Keeping the highest-reliability copy matters downstream: the surviving article is
    what gets cited in the email, so a wire report should outrank an aggregator's
    rewrite of it.
    """
    left_score = reliability.get(left.source_domain, 0.5)
    right_score = reliability.get(right.source_domain, 0.5)
    if left_score != right_score:
        return left if left_score > right_score else right

    if left.published_at and right.published_at:
        return left if left.published_at <= right.published_at else right
    return left if left.published_at else right
