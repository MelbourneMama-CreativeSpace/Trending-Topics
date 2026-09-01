"""Phase 8: the full pipeline (PRD 61, 72, 73, 79, 86, 90).

Only the edges are faked -- collection, the model and the email provider. Deduplication,
clustering, ranking, persistence, locking and rendering all run for real, so these
exercise the actual PRD 79 sequence rather than a sketch of it.
"""

import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.ai.briefing import ResearchedTopic
from app.ai.schemas import BriefSource, SparkIdea
from app.ai.service import AiResult
from app.collect.base import CollectionResult, SourceOutcome
from app.errors import BriefingError, ErrorCode
from app.mailer.sender import SendResult
from app.models import Article, BriefingStatus, EmailStatus, Section, SourceType, build_briefing_id
from app.pipeline import BriefingRunner, PipelineDeps, RunOutcome
from app.storage import Dataset

IST = ZoneInfo("Asia/Kolkata")

HEADLINES = [
    "Central bank holds interest rates steady in split decision",
    "Wildfires force evacuation of towns along the southern coast",
    "Election commission announces the national polling timetable",
    "Merger between two energy firms clears regulatory review",
    "Ferry service suspended after a safety inspection failure",
    "Telugu short film selected for a major international festival",
    "Melbourne film festival announces its programme for the year",
    "Creator economy expands beyond metropolitan cities",
    "Podcast industry revenue grows for a fourth straight year",
    "Filmmaking collective launches a short film fund",
]


class FakeHttp:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def fake_factory():
    return FakeHttp()


def article(title, domain, now):
    from app.collect.normalize import article_id, content_hash

    url = f"https://{domain}/{abs(hash(title)) % 10**8}"
    return Article(
        id=article_id(url), title=title, url=url, source=domain.split(".")[0].title(),
        source_domain=domain, published_at=now - dt.timedelta(hours=2),
        collected_at=now, content_hash=content_hash(title), category="world",
    )


class FakeCollection:
    """Returns real Article objects so dedup, clustering and ranking run for real."""

    def __init__(self, now, global_count=5, niche_count=5, raise_error=None):
        self.now = now
        self.global_count = global_count
        self.niche_count = niche_count
        self.raise_error = raise_error
        self.calls = 0

    async def collect_all(self, http, now, tz):
        self.calls += 1
        if self.raise_error:
            raise self.raise_error

        def build(headlines):
            result = CollectionResult()
            for index, headline in enumerate(headlines):
                # Two outlets per story, so clusters have real breadth.
                for outlet in ("wire.com", "paper.com"):
                    result.articles.append(article(f"{headline} ({outlet})", outlet, now))
                result.outcomes.append(
                    SourceOutcome(f"feed{index}.com", f"Feed {index}", SourceType.NEWS, True, 2)
                )
            return result

        return {
            Section.GLOBAL: build(HEADLINES[: self.global_count]),
            Section.NICHE: build(HEADLINES[5 : 5 + self.niche_count]),
        }


def researched(index, section):
    return ResearchedTopic(
        topic_id=f"t{index}", section=section, headline=f"Researched topic {index}",
        what_happened="Something happened.", why_trending="It is reported widely.",
        why_it_matters="It has consequences.", trend_score=80.0, confidence=0.9,
        category="world",
        sources=[BriefSource(title="A report", url="https://wire.com/a", publisher="Wire")],
        creative_angle="Make a short film." if section is Section.NICHE else None,
    )


class FakeAi:
    def __init__(self, global_count=5, niche_count=5, spark=True, raise_error=None):
        self.global_count = global_count
        self.niche_count = niche_count
        self.spark = spark
        self.raise_error = raise_error

    async def research_all(self, http, global_topics, niche_topics):
        if self.raise_error:
            raise self.raise_error
        return AiResult(
            global_topics=[researched(i, Section.GLOBAL) for i in range(self.global_count)],
            niche_topics=[researched(i + 100, Section.NICHE) for i in range(self.niche_count)],
            attempted=len(global_topics) + len(niche_topics),
        )

    async def creative_spark(self, http, topics):
        if not self.spark:
            return None
        return SparkIdea(idea="An idea.", format="podcast",
                         rationale="Because of today.", confidence=0.8)


class FakeSender:
    def __init__(self, ok=True, error=""):
        self.ok = ok
        self.error = error
        self.sends: list[dict] = []

    async def send(self, client, *, sender, recipient, subject, html, text):
        self.sends.append({"sender": sender, "recipient": recipient,
                           "subject": subject, "html": html, "text": text})
        if self.ok:
            return SendResult(ok=True, message_id="msg_test", attempts=1)
        return SendResult(ok=False, error=self.error or "HTTP 500", attempts=3)


@pytest.fixture
def pipeline_settings(configured_env):
    from app.config import get_settings

    configured_env.setenv("OPENROUTER_API_KEY", "sk-or-test")
    configured_env.setenv("EMAIL_API_KEY", "re_test")
    configured_env.setenv("SENDER_EMAIL", "brief@example.org")
    configured_env.setenv("RECIPIENT_EMAIL", "founder@example.com")
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def build(pipeline_settings, tmp_path, repo):
    """Returns a factory so each test can vary one edge."""
    def _build(collection=None, ai=None, sender=None, now=None):
        moment = now or dt.datetime.now(IST)
        deps = PipelineDeps(
            settings=pipeline_settings,
            repo=repo,
            collection=collection or FakeCollection(moment),
            ai=ai or FakeAi(),
            sender=sender or FakeSender(),
            data_dir=tmp_path / "data",
            http_factory=fake_factory,
            mail_factory=fake_factory,
        )
        return BriefingRunner(deps), deps
    return _build


# --- happy path (PRD 79) -----------------------------------------------------


@pytest.mark.integration
async def test_full_run_completes_and_sends(build):
    runner, deps = build()

    outcome = await runner.run()

    assert outcome.success is True
    assert outcome.status == "completed"
    assert outcome.global_topics == 5
    assert outcome.niche_topics == 5
    assert outcome.email_sent is True
    assert len(deps.sender.sends) == 1


@pytest.mark.integration
async def test_the_sent_email_has_both_parts_and_the_right_recipient(build):
    runner, deps = build()

    await runner.run()

    sent = deps.sender.sends[0]
    assert sent["recipient"] == "founder@example.com"
    assert sent["sender"] == "brief@example.org"
    assert "Morning Intelligence" in sent["subject"]
    assert sent["html"].startswith("<!DOCTYPE html>")
    assert sent["text"].strip()


@pytest.mark.integration
async def test_a_run_persists_its_analysis(build):
    runner, deps = build()

    await runner.run()

    assert deps.repo.read(Dataset.ARTICLES)
    assert deps.repo.read(Dataset.TOPICS)
    assert deps.repo.read(Dataset.TREND_SCORES)
    assert deps.repo.read(Dataset.SOURCES)


@pytest.mark.integration
async def test_the_briefing_record_is_completed_and_sent(build, pipeline_settings):
    runner, deps = build()

    await runner.run()

    record = deps.repo.find_briefing(
        build_briefing_id(dt.datetime.now(IST).date(), pipeline_settings.timezone)
    )
    assert record.status == BriefingStatus.COMPLETED
    assert record.email_status == EmailStatus.SENT
    assert record.email_message_id == "msg_test"
    assert record.completed_at is not None


@pytest.mark.integration
async def test_a_run_finishes_well_inside_the_budget(build):
    """PRD 81 targets under five minutes. With edges faked this is a sanity bound."""
    runner, _ = build()

    outcome = await runner.run()

    assert outcome.duration_seconds < 30


# --- dry run (PRD 86) --------------------------------------------------------


@pytest.mark.integration
async def test_dry_run_builds_everything_and_sends_nothing(build):
    runner, deps = build()

    outcome = await runner.run(dry_run=True)

    assert outcome.success is True
    assert outcome.status == "dry_run"
    assert outcome.email_sent is False
    assert deps.sender.sends == [], "a dry run must not reach the provider"


@pytest.mark.integration
async def test_dry_run_does_not_block_a_later_real_send(build, pipeline_settings):
    """A dry run renders but does not deliver, so the day is not done."""
    runner, deps = build()

    await runner.run(dry_run=True)
    outcome = await runner.run()

    assert outcome.status == "completed"
    assert len(deps.sender.sends) == 1


# --- idempotency and force (PRD 49, 73) --------------------------------------


@pytest.mark.integration
async def test_second_run_of_the_day_is_skipped(build):
    runner, deps = build()

    first = await runner.run()
    second = await runner.run()

    assert first.status == "completed"
    assert second.status == "already_completed"
    assert second.success is True
    assert len(deps.sender.sends) == 1, "the second run must not send"


@pytest.mark.integration
async def test_force_overrides_duplicate_protection(build):
    runner, deps = build()

    await runner.run()
    forced = await runner.run(force=True)

    assert forced.status == "completed"
    assert len(deps.sender.sends) == 2


# --- thresholds (PRD 31) -----------------------------------------------------


@pytest.mark.integration
async def test_too_few_topics_refuses_rather_than_padding(build):
    """PRD 31: three verified topics beat five invented ones, and below three there
    is no briefing worth sending."""
    runner, deps = build(ai=FakeAi(global_count=1, niche_count=1))

    outcome = await runner.run()

    assert outcome.success is False
    assert outcome.error == ErrorCode.NO_USABLE_NEWS.value
    assert deps.sender.sends == [], "nothing should be sent"


@pytest.mark.integration
async def test_a_thin_but_viable_day_is_sent_as_partial(build):
    """PRD 62: four verified niche stories is a valid send."""
    runner, deps = build(ai=FakeAi(global_count=5, niche_count=4))

    outcome = await runner.run()

    assert outcome.success is True
    assert outcome.status == "partial"
    assert outcome.niche_topics == 4
    assert len(deps.sender.sends) == 1
    assert "4 verified creative trends" in deps.sender.sends[0]["html"]


@pytest.mark.integration
async def test_a_partial_run_is_recorded_as_partial(build, pipeline_settings):
    runner, deps = build(ai=FakeAi(global_count=5, niche_count=3))

    await runner.run()

    record = deps.repo.find_briefing(
        build_briefing_id(dt.datetime.now(IST).date(), pipeline_settings.timezone)
    )
    assert record.status == BriefingStatus.PARTIAL


@pytest.mark.integration
async def test_a_missing_spark_is_a_warning_not_a_failure(build):
    """PRD 41: the section is optional."""
    runner, _ = build(ai=FakeAi(spark=False))

    outcome = await runner.run()

    assert outcome.success is True
    assert any("spark" in warning for warning in outcome.warnings)


# --- failures (PRD 59, 60, 61, 90) -------------------------------------------


@pytest.mark.integration
async def test_no_usable_news_fails_without_sending(build):
    error = BriefingError(ErrorCode.NO_USABLE_NEWS, "everything failed")
    runner, deps = build(collection=FakeCollection(dt.datetime.now(IST), raise_error=error))

    outcome = await runner.run()

    assert outcome.success is False
    assert outcome.error == ErrorCode.NO_USABLE_NEWS.value
    assert deps.sender.sends == []


@pytest.mark.integration
async def test_total_ai_failure_fails_without_sending(build):
    error = BriefingError(ErrorCode.AI_PROCESSING_FAILED, "all topics failed")
    runner, deps = build(ai=FakeAi(raise_error=error))

    outcome = await runner.run()

    assert outcome.success is False
    assert outcome.error == ErrorCode.AI_PROCESSING_FAILED.value
    assert deps.sender.sends == []


@pytest.mark.integration
async def test_delivery_failure_is_reported_as_a_failed_run(build):
    """PRD 61: a briefing nobody received is not a success."""
    runner, _ = build(sender=FakeSender(ok=False, error="HTTP 500"))

    outcome = await runner.run()

    assert outcome.success is False
    assert outcome.error == ErrorCode.EMAIL_FAILED.value


@pytest.mark.integration
async def test_delivery_failure_leaves_the_day_retryable(build, pipeline_settings):
    """The record must not claim delivery, or tomorrow's duplicate check skips a
    morning that never arrived."""
    runner, deps = build(sender=FakeSender(ok=False))

    await runner.run()

    briefing_id = build_briefing_id(dt.datetime.now(IST).date(), pipeline_settings.timezone)
    record = deps.repo.find_briefing(briefing_id)
    assert record.email_status == EmailStatus.FAILED
    assert deps.repo.was_already_delivered(briefing_id) is False


@pytest.mark.integration
async def test_an_unexpected_exception_becomes_an_internal_error(build):
    runner, deps = build(ai=FakeAi(raise_error=RuntimeError("something odd")))

    outcome = await runner.run()

    assert outcome.success is False
    assert outcome.error == ErrorCode.INTERNAL_ERROR.value


@pytest.mark.integration
async def test_the_endpoint_never_raises(build):
    """Whatever goes wrong, the caller gets a RunOutcome rather than a traceback."""
    runner, _ = build(collection=FakeCollection(dt.datetime.now(IST),
                                                raise_error=ValueError("boom")))

    assert isinstance(await runner.run(), RunOutcome)


# --- locking and timeout (PRD 71, 72, 90) ------------------------------------


@pytest.mark.integration
async def test_the_lock_is_released_after_a_successful_run(build, tmp_path):
    runner, _ = build()

    await runner.run()

    assert not (tmp_path / "data" / "run.lock").exists()


@pytest.mark.integration
async def test_the_lock_is_released_after_a_failed_run(build, tmp_path):
    """PRD 90: a crash must never orphan the lock and block every later morning."""
    runner, _ = build(ai=FakeAi(raise_error=RuntimeError("boom")))

    await runner.run()

    assert not (tmp_path / "data" / "run.lock").exists()


@pytest.mark.integration
async def test_a_concurrent_run_is_rejected(build, tmp_path):
    from app.locking import ExecutionLock

    runner, _ = build()
    lock_path = tmp_path / "data" / "run.lock"

    with ExecutionLock(lock_path, run_id="other-run"):
        outcome = await runner.run()

    assert outcome.success is False
    assert outcome.error == ErrorCode.RUN_ALREADY_IN_PROGRESS.value


@pytest.mark.integration
async def test_a_timeout_aborts_cleanly_and_releases_the_lock(build, tmp_path):
    """PRD 72: stop expensive work rather than hanging."""
    class SlowAi(FakeAi):
        async def research_all(self, http, global_topics, niche_topics):
            await asyncio.sleep(5)
            return await super().research_all(http, global_topics, niche_topics)

    runner, deps = build(ai=SlowAi())

    outcome = await runner.run(timeout_seconds=1)

    assert outcome.success is False
    assert outcome.error == ErrorCode.EXECUTION_TIMEOUT.value
    assert deps.sender.sends == []
    assert not (tmp_path / "data" / "run.lock").exists()


# --- ordering guarantees (PRD 90) --------------------------------------------


@pytest.mark.integration
async def test_a_csv_write_failure_aborts_before_the_email_is_sent(build):
    """PRD 90: abort before marking success. A run that cannot persist its history
    must not deliver a briefing that tomorrow's momentum will disagree with."""
    runner, deps = build()

    original = deps.repo.write

    def failing_write(dataset, records):
        if dataset is Dataset.TREND_SCORES:
            raise BriefingError(ErrorCode.CSV_WRITE_FAILED, "disk full")
        return original(dataset, records)

    deps.repo.write = failing_write

    outcome = await runner.run()

    assert outcome.success is False
    assert outcome.error == ErrorCode.CSV_WRITE_FAILED.value
    assert deps.sender.sends == [], "the email must not go out"


@pytest.mark.integration
async def test_history_from_one_run_feeds_the_next(build):
    """PRD 26: momentum needs yesterday's scores present for today's ranking."""
    runner, deps = build()

    await runner.run()
    scores = deps.repo.read(Dataset.TREND_SCORES)

    assert scores, "trend scores must persist for tomorrow's momentum"
    assert all(0.0 <= row.trend_score <= 100.0 for row in scores)


@pytest.mark.integration
async def test_retention_runs_before_collection(build, repo):
    """PRD 79 step 6: expired rows go before new ones arrive."""
    from tests.conftest import make_article

    repo.write(Dataset.ARTICLES, [make_article("2020-01-01T00:00:00+05:30", "ancient")])
    runner, deps = build()

    await runner.run()

    assert "ancient" not in {a.id for a in deps.repo.read(Dataset.ARTICLES)}


# --- topic identity across runs (PRD 26) ------------------------------------


class ShiftingCollection(FakeCollection):
    """Same stories, reworded on the second day.

    This is what actually happens in production: a cluster's representative article
    changes, so it mints a different topic_id while still reconciling to yesterday's
    identity. A single run cannot expose an ordering bug between minting and
    reconciliation, because with an empty topics.csv nothing is ever reassigned.
    """

    DAY_TWO = [
        "Central bank interest rates unchanged as governors split",
        "Wildfire evacuation widens across southern coastal towns",
        "Polling timetable confirmed by the election commission",
        "Regulator clears the energy firms merger",
        "Safety inspection failure keeps the ferry service suspended",
        "Telugu short film wins a place at an international festival",
        "Melbourne film festival programme revealed for the year",
        "Creator economy growth shifts to non-metro cities",
        "Podcast revenue climbs for a fourth year",
        "Short film fund launched by a filmmaking collective",
    ]

    async def collect_all(self, http, now, tz):
        if self.calls >= 1:
            original, HEADLINES[:] = HEADLINES[:], self.DAY_TWO
            try:
                return await super().collect_all(http, now, tz)
            finally:
                HEADLINES[:] = original
        return await super().collect_all(http, now, tz)


@pytest.mark.integration
async def test_trend_scores_and_topics_agree_on_topic_ids_across_days(build):
    """Regression, and the reason _persist_analysis pins its ordering.

    reconcile() reassigns a cluster topic_id to the identity it carried yesterday.
    Building the trend-score rows before reconciliation stamped them with the freshly
    minted id while topics.csv recorded the carried-over one. The two files disagreed,
    tomorrow's history lookup matched nothing, and velocity sat at its neutral value
    forever -- momentum silently never worked, and none of it is visible on day one.
    """
    runner, deps = build(collection=ShiftingCollection(dt.datetime.now(IST)))

    await runner.run()
    await runner.run(force=True)

    topic_ids = {t.topic_id for t in deps.repo.read(Dataset.TOPICS)}
    score_ids = {s.topic_id for s in deps.repo.read(Dataset.TREND_SCORES)}

    assert score_ids, "the run must record trend scores"
    orphans = score_ids - topic_ids
    assert not orphans, (
        f"{len(orphans)} scored topics have no matching identity in topics.csv; "
        "tomorrow's momentum lookup would silently find nothing"
    )


@pytest.mark.integration
async def test_yesterdays_scores_are_findable_today(build):
    """The end-to-end form of the same invariant: history must actually resolve."""
    from app.rank.history import load_previous_scores

    runner, deps = build(collection=ShiftingCollection(dt.datetime.now(IST)))
    await runner.run()
    await runner.run(force=True)

    # Age the stored scores by a day so they count as history for the next run.
    aged = deps.repo.read(Dataset.TREND_SCORES)
    for row in aged:
        row.date = row.date - dt.timedelta(days=1)
    deps.repo.write(Dataset.TREND_SCORES, aged)

    today = dt.datetime.now(IST).date()
    history = load_previous_scores(deps.repo, Section.GLOBAL, today)
    known_topics = {t.topic_id for t in deps.repo.read(Dataset.TOPICS)}

    assert history, "yesterday's scores must be loadable"
    assert set(history) <= known_topics, "history keys must match stored topic identities"


@pytest.mark.integration
async def test_only_selected_topics_are_persisted(build):
    """Unselected clusters can never match history, because trend scores exist only
    for selected ones. Storing all ~900 would add megabytes of git history for nothing.
    """
    runner, deps = build()

    await runner.run()

    stored = deps.repo.read(Dataset.TOPICS)
    assert len(stored) <= 10, f"expected at most the selected topics, got {len(stored)}"
