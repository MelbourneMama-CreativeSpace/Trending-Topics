"""Per-topic research and summarisation (PRD 30, 32, 33, 36, 66).

One model call produces research, summary and personalisation together. PRD 84 is
explicit about cost: only the ranked top topics get expensive processing, and splitting
this into three calls per topic would triple that for no gain.

A topic that fails here returns None. PRD 30 requires the other topics to continue, and
PRD 31 requires that we never invent a replacement.
"""

import logging
from dataclasses import dataclass, field

import httpx
from pydantic import ValidationError

from app.ai.client import OpenRouterClient
from app.ai.prompts import GLOBAL_SYSTEM, NICHE_SYSTEM, build_topic_prompt
from app.ai.schemas import BriefSource, TopicBrief
from app.ai.validation import retrieved_sources, scrub_brief, select_sources
from app.errors import BriefingError
from app.logging_setup import LOGGER_NAME
from app.models import Section
from app.rank.context import RankedTopic

# PRD 66: invalid JSON gets one retry, then the topic is marked failed.
VALIDATION_ATTEMPTS = 2


@dataclass
class ResearchedTopic:
    """A topic ready for rendering. Every field here has passed validation."""

    topic_id: str
    section: Section
    headline: str
    what_happened: str
    why_trending: str
    why_it_matters: str
    trend_score: float
    confidence: float
    sources: list[BriefSource]
    creative_angle: str | None = None
    key_facts: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    conflict_detected: bool = False


async def research_topic(
    http: httpx.AsyncClient,
    ai: OpenRouterClient,
    ranked: RankedTopic,
    section: Section,
    logger: logging.Logger | None = None,
) -> ResearchedTopic | None:
    """Research one topic, or return None if it cannot be produced honestly."""
    log = logger or logging.getLogger(LOGGER_NAME)

    sources = retrieved_sources(ranked.cluster)
    if not sources:
        # PRD 34: every source needs a title, URL and publisher. No citable source
        # means no verifiable story, and unverifiable stories are not published.
        log.warning("TOPIC_SKIPPED_NO_SOURCES topic_id=%s", ranked.topic_id)
        return None

    system = GLOBAL_SYSTEM if section is Section.GLOBAL else NICHE_SYSTEM
    prompt = build_topic_prompt(
        ranked.headline,
        [{"title": source.title, "publisher": source.publisher} for source in sources],
        section.value,
    )

    for attempt in range(1, VALIDATION_ATTEMPTS + 1):
        try:
            raw = await ai.complete_json(http, system, prompt)
            brief = TopicBrief.model_validate(raw)
        except BriefingError as exc:
            log.warning(
                "TOPIC_AI_FAILED topic_id=%s attempt=%d code=%s",
                ranked.topic_id,
                attempt,
                exc.code,
            )
            if attempt == VALIDATION_ATTEMPTS:
                return None
            continue
        except ValidationError as exc:
            log.warning(
                "TOPIC_SCHEMA_INVALID topic_id=%s attempt=%d errors=%d",
                ranked.topic_id,
                attempt,
                exc.error_count(),
            )
            if attempt == VALIDATION_ATTEMPTS:
                return None
            continue

        return _assemble(brief, ranked, section, sources)

    return None


def _assemble(
    brief: TopicBrief,
    ranked: RankedTopic,
    section: Section,
    sources: list[BriefSource],
) -> ResearchedTopic:
    scrubbed = scrub_brief(brief, {source.url for source in sources})

    return ResearchedTopic(
        topic_id=ranked.topic_id,
        section=section,
        headline=scrubbed.headline,
        what_happened=scrubbed.what_happened,
        why_trending=scrubbed.why_trending,
        why_it_matters=scrubbed.why_it_matters,
        # PRD 40: the creative angle belongs to the niche section only.
        creative_angle=scrubbed.creative_angle if section is Section.NICHE else None,
        key_facts=scrubbed.key_facts,
        uncertainties=scrubbed.uncertainties,
        conflict_detected=scrubbed.conflict_detected,
        confidence=scrubbed.confidence,
        trend_score=ranked.trend_score,
        sources=select_sources(sources, scrubbed.source_indices),
    )
