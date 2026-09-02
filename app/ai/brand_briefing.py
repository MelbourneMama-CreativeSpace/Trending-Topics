"""Research for Brand Radar entries (PRD 30, 33, 66).

A deliberately cheaper call than `briefing.research_topic`. A brand block is one
scannable line, so the model is asked for a headline and a single sentence instead of
four fields plus facts and uncertainties. Ten brands at full length would roughly
triple both the email and the per-run cost for material nobody reads in full.

Everything that guards the full brief guards this one too: retrieved content is fenced
as untrusted, the schema has no URL field, and citations are rebuilt from articles we
actually collected.
"""

import logging

import httpx
from pydantic import ValidationError

from app.ai.briefing import ResearchedTopic
from app.ai.client import OpenRouterClient
from app.ai.prompts import BRAND_SYSTEM, build_brand_prompt
from app.ai.schemas import BrandBrief
from app.ai.validation import retrieved_sources, scrub_fabricated_urls, select_sources
from app.brands import Brand
from app.collect.urls import canonical_url
from app.errors import BriefingError
from app.logging_setup import LOGGER_NAME
from app.models import Section
from app.rank.context import RankedTopic

VALIDATION_ATTEMPTS = 2

# A brand entry is a headline plus one sentence, so it needs a fraction of the budget
# a full brief does. Still generous enough that a verbose answer is not truncated.
BRAND_MAX_TOKENS = 400


async def research_brand_topic(
    http: httpx.AsyncClient,
    ai: OpenRouterClient,
    ranked: RankedTopic,
    brand: Brand,
    logger: logging.Logger | None = None,
) -> ResearchedTopic | None:
    """Research one Brand Radar entry, or return None if it cannot be produced."""
    log = logger or logging.getLogger(LOGGER_NAME)

    sources = retrieved_sources(ranked.cluster)
    if not sources:
        log.warning("BRAND_TOPIC_SKIPPED brand=%s reason=no_sources", brand.key)
        return None

    prompt = build_brand_prompt(
        brand.name,
        brand.tagline,
        ranked.headline,
        [{"title": s.title, "publisher": s.publisher} for s in sources],
    )

    for attempt in range(1, VALIDATION_ATTEMPTS + 1):
        try:
            raw = await ai.complete_json(http, BRAND_SYSTEM, prompt, max_tokens=BRAND_MAX_TOKENS)
            brief = BrandBrief.model_validate(raw)
        except (BriefingError, ValidationError) as exc:
            log.warning(
                "BRAND_TOPIC_FAILED brand=%s attempt=%d error=%s",
                brand.key,
                attempt,
                getattr(exc, "code", type(exc).__name__),
            )
            if attempt == VALIDATION_ATTEMPTS:
                return None
            continue

        # Canonicalised to match how scrub_fabricated_urls compares, even though
        # collection already stores canonical URLs. Relying on that coincidence
        # would break the moment storage changed.
        allowed = {canonical_url(source.url) for source in sources}
        return ResearchedTopic(
            topic_id=ranked.topic_id,
            section=Section.NICHE,
            headline=scrub_fabricated_urls(brief.headline, allowed),
            what_happened="",
            why_trending="",
            why_it_matters=scrub_fabricated_urls(brief.why_it_matters, allowed),
            trend_score=ranked.trend_score,
            confidence=brief.confidence,
            category=brand.tagline,
            brand_key=brand.key,
            brand_name=brand.name,
            sources=select_sources(sources, brief.source_indices),
        )

    return None
