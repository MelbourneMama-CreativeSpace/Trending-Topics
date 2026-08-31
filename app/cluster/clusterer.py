"""Topic clustering (PRD 25).

Groups surviving articles into the underlying stories they report on, so
"OpenAI launches / announces / new model" becomes one topic rather than three trends.

Leader clustering, not single-linkage: each article is compared against a cluster's
*representative*, never against any member. Single-linkage would chain -- A matches B,
B matches C, so A and C end up together even when they are unrelated -- and on news
headlines that quietly collapses half a day's stories into one blob.
"""

import datetime as dt
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field

from app.cluster.similarity import IdfModel, build_idf, cosine, same_story
from app.cluster.text import token_set
from app.models import Article, Section, Topic

MAX_CLUSTER_SIZE = 60


@dataclass
class Cluster:
    """One underlying story and the articles reporting it."""

    topic_id: str
    headline: str
    section: Section
    articles: list[Article] = field(default_factory=list)
    tokens: frozenset[str] = frozenset()

    @property
    def size(self) -> int:
        return len(self.articles)

    @property
    def source_domains(self) -> set[str]:
        return {article.source_domain for article in self.articles}

    @property
    def representative(self) -> Article:
        return self.articles[0]

    def earliest_published(self) -> dt.datetime | None:
        stamps = [a.published_at for a in self.articles if a.published_at]
        return min(stamps) if stamps else None

    def latest_published(self) -> dt.datetime | None:
        stamps = [a.published_at for a in self.articles if a.published_at]
        return max(stamps) if stamps else None


def make_topic_id(headline: str) -> str:
    """Deterministic id from the representative headline.

    Stable within a run. Continuity *across* runs is handled by matching against
    yesterday's topics in `topics.py`, because a growing cluster can change its
    representative and therefore its minted id.
    """
    from app.cluster.text import tokenize

    basis = " ".join(sorted(set(tokenize(headline))))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def cluster_articles(
    articles: list[Article],
    section: Section,
    reliability: dict[str, float] | None = None,
) -> list[Cluster]:
    """Group articles into topics, most-reported first."""
    if not articles:
        return []

    reliability = reliability or {}
    token_sets = [token_set(article.title) for article in articles]
    idf = build_idf(token_sets)

    order = sorted(
        range(len(articles)),
        key=lambda i: (
            -reliability.get(articles[i].source_domain, 0.5),
            articles[i].published_at.timestamp() if articles[i].published_at else 0.0,
        ),
    )

    clusters: list[Cluster] = []
    # token -> cluster positions, so a new article is only compared against clusters
    # that share at least one word with it.
    index: dict[str, set[int]] = defaultdict(set)

    for position in order:
        article = articles[position]
        tokens = token_sets[position]

        target = _best_cluster(article, tokens, clusters, index, idf)

        if target is None:
            cluster = Cluster(
                topic_id=make_topic_id(article.title),
                headline=article.title,
                section=section,
                articles=[article],
                tokens=tokens,
            )
            clusters.append(cluster)
            for token in tokens:
                index[token].add(len(clusters) - 1)
        else:
            clusters[target].articles.append(article)

    clusters.sort(key=lambda c: (-len(c.source_domains), -c.size))
    return clusters


def _best_cluster(
    article: Article,
    tokens: frozenset[str],
    clusters: list[Cluster],
    index: dict[str, set[int]],
    idf: IdfModel,
) -> int | None:
    """Highest-scoring cluster whose representative is the same story, if any."""
    candidates: set[int] = set()
    for token in tokens:
        candidates |= index.get(token, set())

    best_position: int | None = None
    best_score = 0.0

    for candidate in candidates:
        cluster = clusters[candidate]
        if cluster.size >= MAX_CLUSTER_SIZE:
            continue
        if not same_story(
            article.title, cluster.headline, idf, tokens, cluster.tokens
        ):
            continue

        score = cosine(tokens, cluster.tokens, idf)
        if score > best_score:
            best_score = score
            best_position = candidate

    return best_position


def clusters_to_topics(
    clusters: list[Cluster], now: dt.datetime, description_limit: int = 300
) -> list[Topic]:
    """Project clusters into the topics.csv schema (PRD 13)."""
    return [
        Topic(
            topic_id=cluster.topic_id,
            headline=cluster.headline,
            description=_describe(cluster)[:description_limit],
            section=cluster.section,
            first_seen=cluster.earliest_published() or now,
            last_seen=cluster.latest_published() or now,
            created_at=now,
        )
        for cluster in clusters
    ]


def _describe(cluster: Cluster) -> str:
    others = [a.title for a in cluster.articles[1:3]]
    return " | ".join(others) if others else cluster.headline
