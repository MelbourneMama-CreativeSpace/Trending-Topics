"""Collection orchestration (PRD 58, 59).

Runs every enabled collector for a section, merges results, and records what each
source did. The only failure that stops the pipeline here is total failure: PRD 59
says if RSS, the news API and search all produce nothing, the run must not send a
newsletter at all.
"""

import datetime as dt
import logging
from zoneinfo import ZoneInfo

import httpx

from app.collect.base import CollectionResult, Collector
from app.errors import BriefingError, ErrorCode, Severity
from app.logging_setup import LOGGER_NAME
from app.models import Section


class CollectionService:
    def __init__(self, collectors: list[Collector], logger: logging.Logger | None = None) -> None:
        self._collectors = collectors
        self._log = logger or logging.getLogger(LOGGER_NAME)

    async def collect_section(
        self,
        client: httpx.AsyncClient,
        section: Section,
        collected_at: dt.datetime,
        tz: ZoneInfo,
    ) -> CollectionResult:
        merged = CollectionResult()

        for collector in self._collectors:
            try:
                merged.merge(await collector.collect(client, section, collected_at, tz))
            except Exception as exc:
                # A collector should handle its own failures. If one escapes anyway,
                # the remaining channels still run -- PRD 58.
                self._log.error(
                    "COLLECTOR_FAILED collector=%s section=%s error=%s",
                    collector.name,
                    section.value,
                    exc,
                )

        self._log.info(
            "COLLECTION_SECTION section=%s articles=%d sources_ok=%d sources_failed=%d",
            section.value,
            len(merged.articles),
            merged.succeeded,
            merged.failed,
        )
        return merged

    async def collect_all(
        self, client: httpx.AsyncClient, collected_at: dt.datetime, tz: ZoneInfo
    ) -> dict[Section, CollectionResult]:
        """Collect both sections. Raises only when every channel produced nothing."""
        results = {
            section: await self.collect_section(client, section, collected_at, tz)
            for section in (Section.GLOBAL, Section.NICHE)
        }

        if not any(result.articles for result in results.values()):
            raise BriefingError(
                ErrorCode.NO_USABLE_NEWS,
                "No articles from any collector in either section",
                severity=Severity.CRITICAL,
            )

        return results
