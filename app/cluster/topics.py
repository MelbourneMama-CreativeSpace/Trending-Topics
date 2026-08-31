"""Topic continuity across runs (PRD 13, 26).

A story that ran yesterday and is still running today must keep the same `topic_id`,
otherwise Phase 5 cannot compute momentum: yesterday 40, today 82 is only visible if
both scores hang off one identity.

Cluster ids are minted from the representative headline, and a growing cluster can
change its representative. So identity is resolved by matching today's clusters
against yesterday's topics, not by hoping the mint is stable.
"""

import datetime as dt
from dataclasses import dataclass

from app.cluster.clusterer import Cluster
from app.cluster.similarity import build_idf, cosine, same_story
from app.cluster.text import token_set
from app.models import Topic


@dataclass
class ReconcileReport:
    topics: list[Topic]
    carried_over: int = 0
    newly_seen: int = 0


def reconcile(
    clusters: list[Cluster],
    existing: list[Topic],
    now: dt.datetime,
) -> ReconcileReport:
    """Give each cluster its lasting identity and running first/last seen dates."""
    if not clusters:
        return ReconcileReport(topics=[], carried_over=0, newly_seen=0)

    existing_tokens = {
        topic.topic_id: token_set(topic.headline) for topic in existing
    }

    # One IDF model over both days, so today's headlines and yesterday's are scored
    # on the same vocabulary.
    idf = build_idf(
        [token_set(cluster.headline) for cluster in clusters]
        + list(existing_tokens.values())
    )

    claimed: set[str] = set()
    topics: list[Topic] = []
    carried = 0

    for cluster in clusters:
        tokens = token_set(cluster.headline)
        match = _best_existing(cluster, tokens, existing, existing_tokens, claimed, idf)

        if match is None:
            topics.append(
                Topic(
                    topic_id=cluster.topic_id,
                    headline=cluster.headline,
                    description=_describe(cluster),
                    section=cluster.section,
                    first_seen=cluster.earliest_published() or now,
                    last_seen=now,
                    created_at=now,
                )
            )
            continue

        claimed.add(match.topic_id)
        carried += 1
        # Reuse the established identity and keep the original first_seen, so age and
        # momentum stay measurable.
        cluster.topic_id = match.topic_id
        topics.append(
            Topic(
                topic_id=match.topic_id,
                headline=cluster.headline,
                description=_describe(cluster),
                section=match.section,
                first_seen=min(match.first_seen, cluster.earliest_published() or now),
                last_seen=now,
                created_at=match.created_at,
            )
        )

    return ReconcileReport(
        topics=topics, carried_over=carried, newly_seen=len(topics) - carried
    )


def _best_existing(
    cluster: Cluster,
    tokens: frozenset[str],
    existing: list[Topic],
    existing_tokens: dict[str, frozenset[str]],
    claimed: set[str],
    idf,
) -> Topic | None:
    best: Topic | None = None
    best_score = 0.0

    for topic in existing:
        if topic.topic_id in claimed or topic.section != cluster.section:
            continue

        other = existing_tokens[topic.topic_id]
        if not same_story(cluster.headline, topic.headline, idf, tokens, other):
            continue

        score = cosine(tokens, other, idf)
        if score > best_score:
            best_score = score
            best = topic

    return best


def _describe(cluster: Cluster) -> str:
    others = [article.title for article in cluster.articles[1:3]]
    return (" | ".join(others) if others else cluster.headline)[:300]


def merge_topic_history(existing: list[Topic], current: list[Topic]) -> list[Topic]:
    """Topics table after this run: today's rows, plus untouched history."""
    current_ids = {topic.topic_id for topic in current}
    untouched = [topic for topic in existing if topic.topic_id not in current_ids]
    return [*untouched, *current]
