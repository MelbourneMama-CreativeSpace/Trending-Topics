"""Scoring weights (PRD 19, 22).

Two separate weight sets for two separate engines. They are validated at import time:
a weight table that does not sum to 1.0 silently rescales every score and makes
history incomparable across a deploy.
"""

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class GlobalWeights:
    """PRD 19."""

    recency: float = 0.30
    source_breadth: float = 0.25
    velocity: float = 0.20
    engagement: float = 0.15
    significance: float = 0.10


@dataclass(frozen=True)
class NicheWeights:
    """PRD 22."""

    recency: float = 0.25
    source_breadth: float = 0.20
    velocity: float = 0.20
    engagement: float = 0.15
    industry_importance: float = 0.10
    niche_relevance: float = 0.10


def total(weights: object) -> float:
    return sum(getattr(weights, field.name) for field in fields(weights))


GLOBAL_WEIGHTS = GlobalWeights()
NICHE_WEIGHTS = NicheWeights()

for _table in (GLOBAL_WEIGHTS, NICHE_WEIGHTS):
    if abs(total(_table) - 1.0) > 1e-9:
        raise ValueError(f"{type(_table).__name__} weights must sum to 1.0, got {total(_table)}")
