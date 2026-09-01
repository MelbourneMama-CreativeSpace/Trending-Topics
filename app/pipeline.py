"""The daily briefing pipeline (PRD 79).

Executes the twenty-seven steps in order, behind one lock, with every dependency
injected so the whole sequence can be exercised in tests without touching a network.

The shape of failure handling here follows PRD 90: almost everything degrades. A dead
feed, a failed topic, a thin day -- all of those still produce a briefing. Only three
things stop the run: no usable news at all, every AI call failing, and a delivery
failure, because each of those would mean sending nothing or sending something false.
"""

import asyncio
import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.ai.service import AiService
from app.cluster.clusterer import cluster_articles
from app.cluster.dedup import deduplicate
from app.cluster.topics import merge_topic_history, reconcile
from app.collect.http import build_client
from app.collect.service import CollectionService
from app.collect.sources import reliability_index, update_registry
from app.config import Settings
from app.errors import BriefingError, ErrorCode, Severity
from app.locking import ExecutionLock
from app.logging_setup import LogEvent, RunLogger
from app.mailer import Briefing, EmailSender, NullSender, build_mail_client, build_subject
from app.mailer.render import render_html, render_text
from app.models import (
    Briefing as BriefingRecord,
)
from app.models import (
    BriefingStatus,
    EmailStatus,
    Section,
    Source,
    SourceType,
    build_briefing_id,
)
from app.rank import RankingContext, select_global_top, select_niche_top
from app.rank.history import load_previous_scores
from app.rank.profile import DEFAULT_PROFILE, FounderProfile
from app.run_context import generate_run_id
from app.storage import Dataset, Repository
from app.storage.github_sync import GitHubSync, build_github_client

# PRD 72: the whole request has a ceiling. PRD 81 targets 2-4 minutes, so this leaves
# generous headroom while still failing rather than hanging forever.
DEFAULT_TIMEOUT_SECONDS = 600

# PRD 31: three verified topics beat five padded ones. Below three across both
# sections there is no briefing worth sending.
MIN_TOTAL_TOPICS = 3

# Cap on what articles.csv retains. Nothing reads this file back -- it exists because
# PRD 12 defines the schema -- and every row is committed to git on every run, so 2000
# rows meant 630KB of new git history daily. 400 keeps the schema honest at a fraction
# of the cost.
MAX_STORED_ARTICLES = 400

LOCK_FILENAME = "run.lock"


@dataclass
class PipelineDeps:
    """Everything the runner needs, injected so tests can substitute any of it."""

    settings: Settings
    repo: Repository
    collection: CollectionService
    ai: AiService
    sender: EmailSender
    data_dir: Path
    profile: FounderProfile = DEFAULT_PROFILE
    sync: GitHubSync | None = None
    """Git-backed durability (PRD 8). None means local-only, which is correct for
    tests and for any host with a persistent disk."""
    http_factory: object = None
    mail_factory: object = None
    github_factory: object = None


@dataclass
class RunOutcome:
    """The API response body (PRD 78)."""

    success: bool
    run_id: str
    status: str
    global_topics: int = 0
    niche_topics: int = 0
    email_sent: bool = False
    duration_seconds: float = 0.0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_response(self) -> dict[str, object]:
        body: dict[str, object] = {
            "success": self.success,
            "run_id": self.run_id,
            "status": self.status,
        }
        if self.error:
            body["error"] = self.error
            return body
        body.update({
            "global_topics": self.global_topics,
            "niche_topics": self.niche_topics,
            "email_sent": self.email_sent,
            "duration_seconds": round(self.duration_seconds, 1),
        })
        if self.warnings:
            body["warnings"] = self.warnings
        return body


class BriefingRunner:
    def __init__(self, deps: PipelineDeps, logger: logging.Logger | None = None) -> None:
        self._deps = deps
        self._logger = logger

    async def run(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> RunOutcome:
        """Run today's briefing. Never raises; failures come back as a RunOutcome."""
        settings = self._deps.settings
        now = dt.datetime.now(settings.tz)
        run_id = generate_run_id(settings.tz, now)
        log = RunLogger(run_id, self._logger)
        started = time.monotonic()

        log.event(LogEvent.START, dry_run=dry_run, force=force)

        try:
            return await asyncio.wait_for(
                self._guarded(run_id, log, now, started, dry_run=dry_run, force=force),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            # PRD 72. The lock is released by its context manager as the exception
            # unwinds, so a timeout cannot block tomorrow's run.
            log.error(LogEvent.FAILED, error_code=ErrorCode.EXECUTION_TIMEOUT)
            return RunOutcome(
                success=False, run_id=run_id, status="failed",
                error=ErrorCode.EXECUTION_TIMEOUT.value,
                duration_seconds=time.monotonic() - started,
            )
        except BriefingError as exc:
            log.error(LogEvent.FAILED, error_code=exc.code)
            return RunOutcome(
                success=False, run_id=run_id, status="failed", error=exc.code.value,
                duration_seconds=time.monotonic() - started,
            )
        except Exception as exc:  # noqa: BLE001 - the endpoint must never 500 silently
            log.error(
                LogEvent.FAILED,
                error_code=ErrorCode.INTERNAL_ERROR,
                detail=type(exc).__name__,
            )
            return RunOutcome(
                success=False, run_id=run_id, status="failed",
                error=ErrorCode.INTERNAL_ERROR.value,
                duration_seconds=time.monotonic() - started,
            )

    async def _guarded(
        self,
        run_id: str,
        log: RunLogger,
        now: dt.datetime,
        started: float,
        *,
        dry_run: bool,
        force: bool,
    ) -> RunOutcome:
        deps = self._deps
        settings = deps.settings
        briefing_id = build_briefing_id(now.date(), settings.timezone)

        with ExecutionLock(deps.data_dir / LOCK_FILENAME, run_id=run_id, logger=self._logger):
            # Restore history before anything reads it. A failed pull is survivable:
            # the run continues on local state and only loses momentum accuracy.
            await self._pull_history(log)

            # PRD 48, 73: idempotent for the day unless explicitly forced.
            if not force and deps.repo.was_already_delivered(briefing_id):
                log.event(LogEvent.SKIPPED, reason=ErrorCode.DUPLICATE_BRIEFING.value)
                return RunOutcome(
                    success=True, run_id=run_id, status="already_completed",
                    duration_seconds=time.monotonic() - started,
                )

            try:
                return await self._work(run_id, log, now, started, briefing_id,
                                        dry_run=dry_run)
            finally:
                # PRD 8: the day's history must survive a Render redeploy whether the
                # run succeeded or not. A failed run that never records its failure
                # would be silently re-sent tomorrow.
                await self._push_history(log, now)

    async def _work(
        self,
        run_id: str,
        log: RunLogger,
        now: dt.datetime,
        started: float,
        briefing_id: str,
        *,
        dry_run: bool,
    ) -> RunOutcome:
        deps = self._deps
        settings = deps.settings

        deps.repo.upsert_briefing(BriefingRecord(
            briefing_id=briefing_id, briefing_date=now.date(),
            timezone=settings.timezone, status=BriefingStatus.STARTED, started_at=now,
        ))

        log.event(LogEvent.CLEANUP_STARTED)
        cleanup = deps.repo.cleanup_old_data(now)
        log.event(LogEvent.CLEANUP_COMPLETED,
                  cutoff=cleanup.cutoff_date, removed=cleanup.total_removed)

        reliability, source_types = self._reliability()

        collected = await self._collect(log, now, settings)
        update_registry(
            deps.repo, [o for r in collected.values() for o in r.outcomes], now
        )

        selections, _clusters = self._rank(
            log, collected, reliability, source_types, now
        )
        # Reconciles topic identity, then builds trend scores from the reconciled ids.
        # The order matters -- see the docstring.
        self._persist_analysis(log, collected, selections, now)

        result, spark = await self._research(log, selections)

        total = result.succeeded
        if total < MIN_TOTAL_TOPICS:
            # PRD 31: refuse rather than pad. Fewer than three verified topics is
            # not a briefing, and inventing the rest is never an option.
            raise BriefingError(
                ErrorCode.NO_USABLE_NEWS,
                f"only {total} verified topics, minimum is {MIN_TOTAL_TOPICS}",
                severity=Severity.CRITICAL,
            )

        briefing = Briefing(
            briefing_date=now.date(), timezone=settings.timezone,
            global_topics=result.global_topics, niche_topics=result.niche_topics,
            spark=spark, expected_global=settings.global_top_n,
            expected_niche=settings.niche_top_n, generated_at=now,
        )
        html, text = render_html(briefing), render_text(briefing)
        log.event(LogEvent.EMAIL_RENDERED,
                  html_bytes=len(html), text_bytes=len(text), partial=briefing.is_partial)

        send = await self._send(log, briefing, html, text, dry_run=dry_run)

        status = self._final_status(briefing, dry_run)
        deps.repo.upsert_briefing(BriefingRecord(
            briefing_id=briefing_id, briefing_date=now.date(),
            timezone=settings.timezone,
            status=BriefingStatus.PARTIAL if briefing.is_partial else BriefingStatus.COMPLETED,
            started_at=now, completed_at=dt.datetime.now(settings.tz),
            global_count=len(result.global_topics),
            niche_count=len(result.niche_topics),
            email_status=EmailStatus.SKIPPED if dry_run else EmailStatus.SENT,
            email_message_id=send.message_id,
        ))

        duration = time.monotonic() - started
        log.event(LogEvent.END, status=status, duration=f"{duration:.1f}s")

        return RunOutcome(
            success=True, run_id=run_id, status=status,
            global_topics=len(result.global_topics),
            niche_topics=len(result.niche_topics),
            email_sent=not dry_run, duration_seconds=duration,
            warnings=self._warnings(briefing, spark),
        )

    # --- git-backed history (PRD 8) -----------------------------------------

    async def _pull_history(self, log: RunLogger) -> None:
        if self._deps.sync is None:
            return
        factory = self._deps.github_factory or build_github_client
        try:
            async with factory() as http:
                await self._deps.sync.pull(http)
        except Exception as exc:  # noqa: BLE001
            # History improves ranking; it is never required to produce a briefing.
            log.warning(LogEvent.SKIPPED, reason="history_pull_failed",
                        detail=type(exc).__name__)

    async def _push_history(self, log: RunLogger, now: dt.datetime) -> None:
        if self._deps.sync is None:
            return
        factory = self._deps.github_factory or build_github_client
        message = f"data: briefing {now.date().isoformat()}"
        try:
            async with factory() as http:
                await self._deps.sync.push(http, message)
        except Exception as exc:  # noqa: BLE001
            # Losing one day of history is bad; losing today's briefing to a GitHub
            # outage would be worse.
            log.warning(LogEvent.SKIPPED, reason="history_push_failed",
                        detail=type(exc).__name__)

    # --- steps ---------------------------------------------------------------

    def _reliability(self) -> tuple[dict[str, float], dict[str, SourceType]]:
        stored = [s for s in self._deps.repo.read(Dataset.SOURCES) if isinstance(s, Source)]
        return (
            reliability_index(stored),
            {s.source_domain: s.source_type for s in stored},
        )

    async def _collect(self, log: RunLogger, now: dt.datetime, settings: Settings):
        log.event(LogEvent.COLLECTION_STARTED)
        factory = self._deps.http_factory or build_client
        async with factory() as http:
            collected = await self._deps.collection.collect_all(http, now, settings.tz)
        log.event(
            LogEvent.COLLECTION_COMPLETED,
            collected=sum(len(r.articles) for r in collected.values()),
            sources_failed=sum(r.failed for r in collected.values()),
        )
        return collected

    def _rank(self, log, collected, reliability, source_types, now):
        settings = self._deps.settings
        selections, all_clusters = {}, []

        for section in (Section.GLOBAL, Section.NICHE):
            articles = collected[section].articles
            report = deduplicate(articles, reliability)
            clusters = cluster_articles(report.kept, section, reliability)
            log.event(LogEvent.DEDUP_COMPLETED, section=section.value,
                      before=len(articles), after=len(report.kept))
            log.event(LogEvent.CLUSTERING_COMPLETED,
                      section=section.value, topics=len(clusters))

            history = load_previous_scores(self._deps.repo, section, now.date())
            context = RankingContext(now=now, reliability=reliability,
                                     source_types=source_types, previous_scores=history)

            if section is Section.GLOBAL:
                chosen = select_global_top(clusters, context, top_n=settings.global_top_n)
                log.event(LogEvent.GLOBAL_RANKING_COMPLETED,
                          ranked=len(clusters), selected=len(chosen))
            else:
                chosen = select_niche_top(clusters, context, top_n=settings.niche_top_n,
                                          profile=self._deps.profile)
                log.event(LogEvent.NICHE_RANKING_COMPLETED,
                          ranked=len(clusters), selected=len(chosen))

            selections[section] = chosen
            all_clusters.extend(clusters)

        return selections, all_clusters

    def _persist_analysis(self, log, collected, selections, now) -> None:
        """PRD 90: a CSV write failure must abort before success is recorded.

        These writes happen before the email is sent for exactly that reason -- a run
        that cannot persist its history should not be delivering a briefing that
        tomorrow's momentum calculation will silently disagree with.

        **Reconciliation happens before scores are built.** `reconcile` reassigns a
        cluster's `topic_id` to the identity it carried yesterday, so building the
        trend-score rows first would stamp them with the freshly minted id while
        topics.csv recorded the carried-over one. The two files would disagree, and
        tomorrow's `load_previous_scores` would match nothing -- velocity would sit at
        its neutral value forever and momentum would silently never work. That failure
        is invisible on day one, which is exactly why the order is pinned by a test.

        Only *selected* topics are persisted. Trend scores exist only for topics that
        were selected, so an unselected cluster can never match history -- storing all
        ~900 of them per run would add megabytes of git history for nothing.
        """
        repo = self._deps.repo
        chosen = [topic for section_topics in selections.values() for topic in section_topics]

        existing = list(repo.read(Dataset.TOPICS))
        reconciled = reconcile([topic.cluster for topic in chosen], existing, now)
        repo.write(Dataset.TOPICS, merge_topic_history(existing, reconciled.topics))
        log.event(LogEvent.CLUSTERING_COMPLETED, carried_over=reconciled.carried_over,
                  newly_seen=reconciled.newly_seen)

        scores = [
            topic.to_trend_score(now.date(), section)
            for section, section_topics in selections.items()
            for topic in section_topics
        ]
        repo.append(Dataset.TREND_SCORES, scores)

        repo.write(
            Dataset.ARTICLES,
            [a for r in collected.values() for a in r.articles][:MAX_STORED_ARTICLES],
        )

    async def _research(self, log: RunLogger, selections):
        log.event(LogEvent.RESEARCH_STARTED)
        factory = self._deps.http_factory or build_client
        async with factory() as http:
            result = await self._deps.ai.research_all(
                http, selections[Section.GLOBAL], selections[Section.NICHE]
            )
            spark = await self._deps.ai.creative_spark(
                http, result.global_topics + result.niche_topics
            )
        log.event(LogEvent.RESEARCH_COMPLETED,
                  succeeded=result.succeeded, failed=result.failed, spark=bool(spark))
        return result, spark

    async def _send(self, log: RunLogger, briefing: Briefing, html: str, text: str,
                    *, dry_run: bool):
        settings = self._deps.settings
        sender = NullSender(self._logger) if dry_run else self._deps.sender
        factory = self._deps.mail_factory or build_mail_client

        async with factory() as http:
            result = await sender.send(
                http,
                sender=settings.sender_email or "",
                recipient=settings.recipient_email or "",
                subject=build_subject(briefing.briefing_date),
                html=html,
                text=text,
            )

        if not result.ok:
            # PRD 61: the briefing record keeps email_status=failed and the API
            # reports failure, so GitHub Actions surfaces a missed morning.
            self._record_email_failure(briefing, result)
            raise BriefingError(ErrorCode.EMAIL_FAILED, result.error, severity=Severity.CRITICAL)

        log.event(LogEvent.EMAIL_SENT, message_id=result.message_id, dry_run=dry_run)
        return result

    def _record_email_failure(self, briefing: Briefing, result) -> None:
        settings = self._deps.settings
        briefing_id = build_briefing_id(briefing.briefing_date, settings.timezone)
        existing = self._deps.repo.find_briefing(briefing_id)
        if existing is None:
            return
        existing.status = BriefingStatus.FAILED
        existing.email_status = EmailStatus.FAILED
        existing.error_code = ErrorCode.EMAIL_FAILED.value
        existing.global_count = len(briefing.global_topics)
        existing.niche_count = len(briefing.niche_topics)
        self._deps.repo.upsert_briefing(existing)

    def _final_status(self, briefing: Briefing, dry_run: bool) -> str:
        if dry_run:
            return "dry_run"
        return "partial" if briefing.is_partial else "completed"

    def _warnings(self, briefing: Briefing, spark) -> list[str]:
        warnings = []
        if len(briefing.global_topics) < briefing.expected_global:
            warnings.append(
                f"only {len(briefing.global_topics)} of {briefing.expected_global} "
                f"global topics were verified"
            )
        if len(briefing.niche_topics) < briefing.expected_niche:
            warnings.append(
                f"only {len(briefing.niche_topics)} of {briefing.expected_niche} "
                f"creative topics were verified"
            )
        if spark is None:
            warnings.append("no creative spark was produced")
        return warnings


def build_http_client() -> httpx.AsyncClient:
    return build_client()
