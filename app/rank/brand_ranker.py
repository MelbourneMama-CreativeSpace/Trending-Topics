"""Per-brand ranking for the Brand Radar (supersedes the single niche list).

Each brand gets its own scored shortlist. The PRD 22 weights are reused unchanged --
recency, breadth, velocity, engagement, industry importance -- with brand relevance
substituted for the founder-wide relevance term.

Two things make this different from just running `rank_niche` ten times:

* **Relevance uses provenance, not only wording.** An article collected from a search
  for "smartphone launch India" is evidence for The Tech Gun regardless of whether its
  headline happens to contain a matching term. Feeds tag their articles with the brand
  key that asked for them, and that tag is treated as first-class evidence.

* **A story belongs to one brand.** Assignment is global and greedy: every plausible
  (brand, story) pair is scored, the strongest pairs win first, and a story taken by
  one brand is gone from the others. Without that, a cricket story lands in both
  Sariggaa Choodu and Cheptha Vintava and the reader sees the same item twice.
"""

from dataclasses import dataclass, field

from app.brands import BRANDS, Brand
from app.cluster.clusterer import Cluster
from app.rank.context import RankedTopic, RankingContext
from app.rank.signals import (
    engagement_score,
    industry_importance_score,
    recency_score,
    source_breadth_score,
    velocity_score,
)
from app.rank.weights import NICHE_WEIGHTS

BRAND_CATEGORY_PREFIX = "brand:"

# Two strong interest terms saturate the wording signal, matching the founder profile.
RELEVANCE_SATURATION = 2.0

# A brand's own queries supplying this share of a cluster's articles saturates the
# provenance signal.
PROVENANCE_SATURATION = 0.5

# Ceiling on relevance that provenance alone can earn.
#
# Provenance started out as conclusive evidence, and a live run showed that is too
# generous: a Google News query for "food festival Australia" returned a story about a
# *film* festival, which then scored 100 for Eat Post Share on provenance alone despite
# sharing no wording with the brand. Search results are looser than the query implies.
#
# Capped below 100, a provenance-only match still beats an empty lane on a quiet day
# but always loses to a story whose wording genuinely matches the brand.
PROVENANCE_ONLY_CEILING = 60.0

# Brand Radar entries must have some connection to their brand, for the same reason
# Creative Radar did: at 10% of the weight, relevance alone cannot keep an unrelated
# story out of a section that is defined by relevance.
MIN_RELEVANCE_FOR_SELECTION = 1.0

DEFAULT_TOPICS_PER_BRAND = 2


@dataclass
class BrandSelection:
    """One brand's block in the briefing."""

    brand: Brand
    topics: list[RankedTopic] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.topics


def provenance_share(cluster: Cluster, brand: Brand) -> float:
    """Fraction of a cluster's articles that this brand's own queries returned."""
    if not cluster.articles:
        return 0.0
    tag = f"{BRAND_CATEGORY_PREFIX}{brand.key}"
    matching = sum(1 for article in cluster.articles if article.category == tag)
    return matching / len(cluster.articles)


def brand_relevance_score(cluster: Cluster, brand: Brand) -> float:
    """How strongly a story belongs to one brand, in 0-100.

    Wording is the primary signal. Provenance -- the story arrived because this
    brand's own query asked for it -- supports it, but cannot reach full relevance on
    its own, because a search engine returns looser matches than the query implies.
    """
    text = " ".join([cluster.headline, *(a.title for a in cluster.articles[:8])])
    matched = brand.matched_terms(text)
    wording = 100.0 * min(1.0, sum(matched.values()) / RELEVANCE_SATURATION) if matched else 0.0

    share = min(1.0, provenance_share(cluster, brand) / PROVENANCE_SATURATION)
    provenance = PROVENANCE_ONLY_CEILING * share

    return max(wording, provenance)


def score_for_brand(
    cluster: Cluster, context: RankingContext, brand: Brand
) -> RankedTopic:
    """PRD 22 weighting, with brand relevance in place of founder relevance."""
    weights = NICHE_WEIGHTS

    components = {
        "recency": recency_score(cluster, context.now),
        "source_breadth": source_breadth_score(cluster, context.reliability),
        "engagement": engagement_score(cluster),
        "industry_importance": industry_importance_score(
            cluster, context.reliability, context.source_types
        ),
        "niche_relevance": brand_relevance_score(cluster, brand),
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


def select_brand_radar(
    clusters: list[Cluster],
    context: RankingContext,
    per_brand: int = DEFAULT_TOPICS_PER_BRAND,
    brands: tuple[Brand, ...] = BRANDS,
    min_relevance: float = MIN_RELEVANCE_FOR_SELECTION,
) -> list[BrandSelection]:
    """Assign stories to brands, strongest pairing first, no story used twice.

    Returns one entry per brand in registry order, including brands with nothing
    today. An empty block is honest: it says the day had nothing for that lane, which
    is more useful than padding it with a story that does not belong.
    """
    if not clusters:
        return [BrandSelection(brand=brand) for brand in brands]

    pairings = []
    for brand in brands:
        for cluster in clusters:
            relevance = brand_relevance_score(cluster, brand)
            if relevance < min_relevance:
                continue
            ranked = score_for_brand(cluster, context, brand)
            pairings.append((ranked.trend_score, relevance, brand.key, cluster.topic_id, ranked))

    # Sort by score, then relevance, then ids so a tie resolves the same way every run.
    pairings.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))

    selections = {brand.key: BrandSelection(brand=brand) for brand in brands}
    claimed: set[str] = set()

    for _score, _relevance, brand_key, topic_id, ranked in pairings:
        if topic_id in claimed:
            continue
        selection = selections[brand_key]
        if len(selection.topics) >= per_brand:
            continue
        selection.topics.append(ranked)
        claimed.add(topic_id)

    return [selections[brand.key] for brand in brands]
