"""AI orchestration (PRD 30, 41, 60, 82).

Topics are processed concurrently but with a bounded semaphore. PRD 82 caps this at
three in flight: the point is to fit inside the five-minute budget without tripping
rate limits at the gateway.
"""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx
from pydantic import ValidationError

from app.ai.briefing import ResearchedTopic, research_topic
from app.ai.client import OpenRouterClient
from app.ai.prompts import SPARK_SYSTEM, build_spark_prompt
from app.ai.schemas import SparkIdea
from app.errors import BriefingError, ErrorCode, Severity
from app.logging_setup import LOGGER_NAME
from app.models import Section
from app.rank.context import RankedTopic

MAX_CONCURRENT_RESEARCH = 3

# PRD 41: the Creative Spark is optional. Below this the idea is not worth sending.
MIN_SPARK_CONFIDENCE = 0.4

# The Spark was marginal at 600: it fit on some runs and truncated on others, which
# is the worst kind of limit because it looks like flakiness rather than a setting.
SPARK_MAX_TOKENS = 900


@dataclass
class AiResult:
    global_topics: list[ResearchedTopic] = field(default_factory=list)
    niche_topics: list[ResearchedTopic] = field(default_factory=list)
    attempted: int = 0

    @property
    def succeeded(self) -> int:
        return len(self.global_topics) + len(self.niche_topics)

    @property
    def failed(self) -> int:
        return self.attempted - self.succeeded


class AiService:
    def __init__(
        self,
        ai: OpenRouterClient,
        concurrency: int = MAX_CONCURRENT_RESEARCH,
        logger: logging.Logger | None = None,
    ) -> None:
        self._ai = ai
        self._concurrency = concurrency
        self._semaphore = asyncio.Semaphore(concurrency)
        self._log = logger or logging.getLogger(LOGGER_NAME)

    async def research_all(
        self,
        http: httpx.AsyncClient,
        global_topics: list[RankedTopic],
        niche_topics: list[RankedTopic],
    ) -> AiResult:
        """Research both sections concurrently, dropping whatever fails.

        Raises only when every topic failed (PRD 60). One failure, or four, is a
        partial briefing rather than a dead run.
        """
        jobs: list[tuple[RankedTopic, Section]] = [
            (topic, Section.GLOBAL) for topic in global_topics
        ]
        jobs += [(topic, Section.NICHE) for topic in niche_topics]

        if not jobs:
            return AiResult()

        outcomes = await asyncio.gather(
            *(self._one(http, topic, section) for topic, section in jobs)
        )

        result = AiResult(attempted=len(jobs))
        for researched, (_, section) in zip(outcomes, jobs, strict=True):
            if researched is None:
                continue
            if section is Section.GLOBAL:
                result.global_topics.append(researched)
            else:
                result.niche_topics.append(researched)

        self._log.info(
            "RESEARCH_COMPLETED attempted=%d succeeded=%d failed=%d",
            result.attempted,
            result.succeeded,
            result.failed,
        )

        if result.succeeded == 0:
            raise BriefingError(
                ErrorCode.AI_PROCESSING_FAILED,
                f"all {result.attempted} topics failed AI processing",
                severity=Severity.CRITICAL,
            )

        return result

    async def _one(
        self, http: httpx.AsyncClient, ranked: RankedTopic, section: Section
    ) -> ResearchedTopic | None:
        async with self._semaphore:
            return await research_topic(http, self._ai, ranked, section, self._log)

    async def creative_spark(
        self, http: httpx.AsyncClient, topics: list[ResearchedTopic]
    ) -> SparkIdea | None:
        """One creative opportunity, or None (PRD 41).

        None is a valid outcome. PRD 41 says do not force an idea, so a low-confidence
        answer is discarded rather than printed.
        """
        if not topics:
            return None

        prompt = build_spark_prompt([topic.headline for topic in topics])

        try:
            raw = await self._ai.complete_json(
                http, SPARK_SYSTEM, prompt, max_tokens=SPARK_MAX_TOKENS
            )
            spark = SparkIdea.model_validate(raw)
        except BriefingError as exc:
            # The code, not just the class name: "BriefingError" alone tells an
            # operator nothing about whether this was a timeout, a rate limit or a
            # malformed response.
            self._log.warning("SPARK_FAILED code=%s detail=%s", exc.code, exc)
            return None
        except ValidationError as exc:
            self._log.warning("SPARK_SCHEMA_INVALID errors=%d", exc.error_count())
            return None

        if spark.confidence < MIN_SPARK_CONFIDENCE:
            self._log.info("SPARK_SKIPPED confidence=%.2f", spark.confidence)
            return None

        return spark
