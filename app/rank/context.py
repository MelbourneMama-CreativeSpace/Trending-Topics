"""Ranking inputs shared by both engines.

Note what is *absent*: there is no founder profile here. `RankingContext` is the only
argument the global ranker takes besides its clusters, so PRD 17's separation is a
property of the type signature rather than a convention someone has to remember.
"""

import datetime as dt
from dataclasses import dataclass, field

from app.cluster.clusterer import Cluster
from app.models import Section, SourceType, TrendScore


@dataclass(frozen=True)
class RankingContext:
    now: dt.datetime
    reliability: dict[str, float] = field(default_factory=dict)
    source_types: dict[str, SourceType] = field(default_factory=dict)
    previous_scores: dict[str, float] = field(default_factory=dict)
    """topic_id -> yesterday's trend_score. Empty on a first run; never required."""

    def previous_for(self, topic_id: str) -> float | None:
        return self.previous_scores.get(topic_id)


@dataclass
class RankedTopic:
    cluster: Cluster
    trend_score: float
    components: dict[str, float]

    @property
    def topic_id(self) -> str:
        return self.cluster.topic_id

    @property
    def headline(self) -> str:
        return self.cluster.headline

    def to_trend_score(self, day: dt.date, section: Section) -> TrendScore:
        """Project into the trend_scores.csv schema (PRD 14)."""
        return TrendScore(
            topic_id=self.cluster.topic_id,
            date=day,
            section=section,
            trend_score=round(self.trend_score, 4),
            recency_score=round(self.components.get("recency", 0.0), 4),
            source_breadth_score=round(self.components.get("source_breadth", 0.0), 4),
            velocity_score=round(self.components.get("velocity", 0.0), 4),
            engagement_score=round(self.components.get("engagement", 0.0), 4),
            significance_score=round(self.components.get("significance", 0.0), 4),
            # PRD 14: populated for niche ranking only; global rows carry 0.
            relevance_score=round(self.components.get("niche_relevance", 0.0), 4),
        )
