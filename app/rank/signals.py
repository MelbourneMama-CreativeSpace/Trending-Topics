"""Individual ranking signals (PRD 19, 22), each normalised to 0-100.

Two of these are genuine measurements and the rest are proxies, which matters when
reading the numbers:

* **recency** and **source breadth** are measured directly from collected data.
* **velocity** is measured, but only once a topic has history to compare against.
* **engagement** is a proxy. This system has no social APIs, so it stands on how many
  outlets carried a story and whether it surfaced in Google Trends -- the latter is
  real search-volume evidence, the former is not engagement at all.
* **significance** and **industry importance** are editorial proxies built from source
  quality and cross-desk spread.

Calling a proxy a measurement is how a ranking engine ends up confidently wrong, so
the distinction is kept explicit here rather than buried.
"""

import datetime as dt
import math

from app.cluster.clusterer import Cluster
from app.models import SourceType

# --- recency ----------------------------------------------------------------

RECENCY_HALF_LIFE_HOURS = 8.0

# A cluster with no publication date anywhere is unknown, not fresh. Scoring it 100
# would let undated feeds dominate the briefing.
UNKNOWN_RECENCY_SCORE = 50.0

# --- breadth ----------------------------------------------------------------

BREADTH_SATURATION = 1.5

# --- engagement -------------------------------------------------------------

ENGAGEMENT_SIZE_SCALE = 2.0
TRENDS_CATEGORY = "trends"
TRENDS_EVIDENCE_FLOOR = 70.0

# --- significance -----------------------------------------------------------

SIGNIFICANCE_QUALITY_SHARE = 60.0
SIGNIFICANCE_SPREAD_SHARE = 40.0
SIGNIFICANCE_SPREAD_TARGET = 3

# --- relevance --------------------------------------------------------------

RELEVANCE_SATURATION = 2.0

INDUSTRY_TYPES = frozenset({SourceType.INDUSTRY, SourceType.OFFICIAL})


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def recency_score(cluster: Cluster, now: dt.datetime) -> float:
    """Exponential decay on the newest report in the cluster.

    Half-life of 8 hours: fresh this morning scores ~100, yesterday morning ~12.
    """
    latest = cluster.latest_published()
    if latest is None:
        return UNKNOWN_RECENCY_SCORE

    age_hours = max(0.0, (now - latest).total_seconds() / 3600.0)
    return _clamp(100.0 * math.exp(-age_hours / RECENCY_HALF_LIFE_HOURS * math.log(2)))


def source_breadth_score(cluster: Cluster, reliability: dict[str, float]) -> float:
    """Reliability-weighted count of distinct domains carrying the story.

    Reliability is *squared* before summing, so the score is dominated by source
    quality rather than headcount. With a linear sum, ten content farms at 0.3 would
    outrank two wire services at 0.95 -- which is exactly backwards, since those ten
    are usually all reprinting the same wire copy.
    """
    weighted = sum(reliability.get(domain, 0.5) ** 2 for domain in cluster.source_domains)
    return _clamp(100.0 * (1.0 - math.exp(-weighted / BREADTH_SATURATION)))


def engagement_score(cluster: Cluster) -> float:
    """Proxy. How widely carried, plus direct evidence of search interest.

    An appearance in Google Trends is the only real engagement signal available here,
    so it sets a floor rather than adding a bonus.
    """
    size_component = 100.0 * (1.0 - math.exp(-(cluster.size - 1) / ENGAGEMENT_SIZE_SCALE))

    in_trends = any(article.category == TRENDS_CATEGORY for article in cluster.articles)
    floor = TRENDS_EVIDENCE_FLOOR if in_trends else 0.0

    return _clamp(max(size_component, floor))


def significance_score(cluster: Cluster, reliability: dict[str, float]) -> float:
    """Proxy for how much the story matters, from source quality and desk spread.

    A story carried by a wire service *and* appearing across several desks (world,
    business, technology) is structurally bigger than one confined to a single desk.
    """
    best_source = max(
        (reliability.get(domain, 0.5) for domain in cluster.source_domains), default=0.5
    )

    desks = {article.category for article in cluster.articles if article.category}
    spread = min(1.0, max(0, len(desks) - 1) / (SIGNIFICANCE_SPREAD_TARGET - 1))

    return _clamp(
        SIGNIFICANCE_QUALITY_SHARE * best_source + SIGNIFICANCE_SPREAD_SHARE * spread
    )


def industry_importance_score(
    cluster: Cluster,
    reliability: dict[str, float],
    source_types: dict[str, SourceType],
) -> float:
    """Niche only. Weight of specialist trade coverage behind the story.

    Variety reporting something carries more weight inside this niche than a general
    news desk mentioning it in passing.
    """
    trade_weight = sum(
        reliability.get(domain, 0.5)
        for domain in cluster.source_domains
        if source_types.get(domain) in INDUSTRY_TYPES
    )
    return _clamp(100.0 * (1.0 - math.exp(-trade_weight / BREADTH_SATURATION)))


def niche_relevance_score(cluster: Cluster, profile) -> float:
    """Niche only. How strongly the story sits inside the founder's world (PRD 37).

    Deliberately not forced: PRD 39 permits an honest low score. A story with no
    matching interest term scores zero rather than being nudged upward.
    """
    text = " ".join([cluster.headline, *(a.title for a in cluster.articles[:8])])
    matched = profile.matched_terms(text)
    if not matched:
        return 0.0

    return _clamp(100.0 * min(1.0, sum(matched.values()) / RELEVANCE_SATURATION))


def velocity_score(base_score: float, previous_score: float | None) -> float:
    """Momentum against yesterday (PRD 26).

    Yesterday 40, today 82 is a rise of 42 and scores 71; a flat topic scores 50.
    A topic with no history scores 50 too -- zero known movement. Treating an absent
    history as a rise from zero would make every first sighting look explosive and
    would flood the briefing on the first run after any data loss.
    """
    if previous_score is None:
        return 50.0
    return _clamp(50.0 + (base_score - previous_score) / 2.0)
