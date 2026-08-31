"""Global trend ranking (PRD 17, 19, 23).

This module must stay blind to the founder's interests. It does not import
`app.rank.profile`, and `rank_global` takes no profile argument -- both enforced by
tests. If all five strongest global trends turn out to be technology stories, PRD 18
says that is fine; what is not fine is Telugu cinema reaching Global Pulse because we
went looking for it.
"""

from app.cluster.clusterer import Cluster
from app.rank.context import RankedTopic, RankingContext
from app.rank.signals import (
    engagement_score,
    recency_score,
    significance_score,
    source_breadth_score,
    velocity_score,
)
from app.rank.weights import GLOBAL_WEIGHTS


def score_global(cluster: Cluster, context: RankingContext) -> RankedTopic:
    """Score one cluster on the PRD 19 weighting."""
    weights = GLOBAL_WEIGHTS

    components = {
        "recency": recency_score(cluster, context.now),
        "source_breadth": source_breadth_score(cluster, context.reliability),
        "engagement": engagement_score(cluster),
        "significance": significance_score(cluster, context.reliability),
    }

    # Velocity compares today against yesterday, so it needs today's standing first.
    # The base is the other four components renormalised to sum to 1.
    base_weight = 1.0 - weights.velocity
    base = (
        weights.recency * components["recency"]
        + weights.source_breadth * components["source_breadth"]
        + weights.engagement * components["engagement"]
        + weights.significance * components["significance"]
    ) / base_weight

    components["velocity"] = velocity_score(base, context.previous_for(cluster.topic_id))

    total = (
        weights.recency * components["recency"]
        + weights.source_breadth * components["source_breadth"]
        + weights.velocity * components["velocity"]
        + weights.engagement * components["engagement"]
        + weights.significance * components["significance"]
    )

    return RankedTopic(cluster=cluster, trend_score=total, components=components)


def rank_global(clusters: list[Cluster], context: RankingContext) -> list[RankedTopic]:
    """Rank global topics, strongest first. Deterministic for a fixed input."""
    ranked = [score_global(cluster, context) for cluster in clusters]
    ranked.sort(key=lambda topic: (-topic.trend_score, topic.topic_id))
    return ranked


def select_global_top(
    clusters: list[Cluster], context: RankingContext, top_n: int = 5
) -> list[RankedTopic]:
    return rank_global(clusters, context)[:top_n]
