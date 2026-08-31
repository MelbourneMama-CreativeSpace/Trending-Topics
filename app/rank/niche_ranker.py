"""Niche trend ranking (PRD 20, 22, 23).

The niche engine is the only place the founder profile is used, and it is used only
*after* clustering -- the profile shapes which niche stories rank highest, never which
articles were collected or how they were grouped.

Kept as a separate call path from `rank_global` on purpose (PRD 23): ranking both
sections through one function is exactly the contamination the PRD warns about.
"""

from app.cluster.clusterer import Cluster
from app.rank.context import RankedTopic, RankingContext
from app.rank.profile import DEFAULT_PROFILE, FounderProfile
from app.rank.signals import (
    engagement_score,
    industry_importance_score,
    niche_relevance_score,
    recency_score,
    source_breadth_score,
    velocity_score,
)
from app.rank.weights import NICHE_WEIGHTS


def score_niche(
    cluster: Cluster,
    context: RankingContext,
    profile: FounderProfile = DEFAULT_PROFILE,
) -> RankedTopic:
    """Score one cluster on the PRD 22 weighting."""
    weights = NICHE_WEIGHTS

    components = {
        "recency": recency_score(cluster, context.now),
        "source_breadth": source_breadth_score(cluster, context.reliability),
        "engagement": engagement_score(cluster),
        "industry_importance": industry_importance_score(
            cluster, context.reliability, context.source_types
        ),
        "niche_relevance": niche_relevance_score(cluster, profile),
    }

    base_weight = 1.0 - weights.velocity
    base = (
        weights.recency * components["recency"]
        + weights.source_breadth * components["source_breadth"]
        + weights.engagement * components["engagement"]
        + weights.industry_importance * components["industry_importance"]
        + weights.niche_relevance * components["niche_relevance"]
    ) / base_weight

    components["velocity"] = velocity_score(base, context.previous_for(cluster.topic_id))

    total = (
        weights.recency * components["recency"]
        + weights.source_breadth * components["source_breadth"]
        + weights.velocity * components["velocity"]
        + weights.engagement * components["engagement"]
        + weights.industry_importance * components["industry_importance"]
        + weights.niche_relevance * components["niche_relevance"]
    )

    return RankedTopic(cluster=cluster, trend_score=total, components=components)


def rank_niche(
    clusters: list[Cluster],
    context: RankingContext,
    profile: FounderProfile = DEFAULT_PROFILE,
) -> list[RankedTopic]:
    """Rank niche topics, strongest first. Deterministic for a fixed input."""
    ranked = [score_niche(cluster, context, profile) for cluster in clusters]
    ranked.sort(key=lambda topic: (-topic.trend_score, topic.topic_id))
    return ranked


# Eligibility gate for Creative Radar. See `select_niche_top`.
MIN_RELEVANCE_FOR_SELECTION = 1.0


def select_niche_top(
    clusters: list[Cluster],
    context: RankingContext,
    top_n: int = 5,
    profile: FounderProfile = DEFAULT_PROFILE,
    min_relevance: float = MIN_RELEVANCE_FOR_SELECTION,
) -> list[RankedTopic]:
    """Top niche topics, excluding any with no connection to the founder at all.

    The PRD 22 weights are applied unchanged -- relevance stays at 10%. This is a
    separate eligibility gate, and it exists because a live run put "Love Island USA
    Returns for Season 8 Reunion" at the top of Creative Radar: zero founder
    relevance, winning purely on trade-press coverage and recency. At 10% of the
    weight, relevance cannot by itself keep an unrelated story out.

    PRD 2 defines this section as the five most *relevant* trending topics for Telugu
    cinema, filmmaking, podcasts and the Melbourne creative scene. On that reading a
    topic matching none of those interests is not a weak member of Creative Radar; it
    is not a member. Ranking still scores every topic, and trend_scores.csv still
    records them -- only selection is gated.

    Returning fewer than `top_n` is the correct outcome when the day is thin. PRD 31
    is explicit that three verified topics beat five padded ones.
    """
    ranked = rank_niche(clusters, context, profile)
    return [
        topic
        for topic in ranked
        if topic.components.get("niche_relevance", 0.0) >= min_relevance
    ][:top_n]
