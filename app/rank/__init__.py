"""Trend ranking (PRD 17-23, 26).

Two engines, two call paths, deliberately not unified.
"""

from app.rank.brand_ranker import (
    BrandSelection,
    brand_relevance_score,
    score_for_brand,
    select_brand_radar,
)
from app.rank.context import RankedTopic, RankingContext
from app.rank.global_ranker import rank_global, score_global, select_global_top
from app.rank.history import load_previous_scores
from app.rank.niche_ranker import rank_niche, score_niche, select_niche_top
from app.rank.profile import DEFAULT_PROFILE, FounderProfile
from app.rank.weights import GLOBAL_WEIGHTS, NICHE_WEIGHTS

__all__ = [
    "DEFAULT_PROFILE",
    "GLOBAL_WEIGHTS",
    "NICHE_WEIGHTS",
    "FounderProfile",
    "BrandSelection",
    "RankedTopic",
    "RankingContext",
    "load_previous_scores",
    "rank_global",
    "rank_niche",
    "score_global",
    "score_niche",
    "select_global_top",
    "brand_relevance_score",
    "score_for_brand",
    "select_brand_radar",
    "select_niche_top",
]
