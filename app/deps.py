"""Wiring the real pipeline together (PRD 54).

Kept apart from `pipeline.py` so the runner has no idea which concrete collectors,
model or email provider it is driving -- which is what lets the integration tests
drive the whole sequence without a network.
"""

import logging
from pathlib import Path

from app.ai.client import OpenRouterClient
from app.ai.service import AiService
from app.collect.feeds import GLOBAL_FEEDS, NICHE_FEEDS, niche_search_feeds
from app.collect.newsapi import NewsApiCollector
from app.collect.rss import RssCollector
from app.collect.service import CollectionService
from app.collect.websearch import WebSearchCollector
from app.config import Settings
from app.errors import BriefingError, ErrorCode, Severity
from app.logging_setup import LOGGER_NAME
from app.mailer import ResendSender
from app.models import Section
from app.pipeline import BriefingRunner, PipelineDeps
from app.storage import LocalCsvBackend, Repository
from app.storage.github_sync import GitHubSync

DATA_DIR = Path("data")


def build_runner(
    settings: Settings, data_dir: Path = DATA_DIR, logger: logging.Logger | None = None
) -> BriefingRunner:
    """Assemble the production pipeline, or refuse with a clear reason."""
    missing = settings.missing_pipeline_config()
    if missing:
        # Fail before doing any work, naming every absent variable at once so the
        # operator fixes them in one pass rather than one per attempt.
        raise BriefingError(
            ErrorCode.CONFIG_INVALID,
            f"missing configuration: {', '.join(missing)}",
            severity=Severity.FATAL,
        )

    log = logger or logging.getLogger(LOGGER_NAME)

    collectors = [
        RssCollector(
            {
                Section.GLOBAL: GLOBAL_FEEDS,
                Section.NICHE: NICHE_FEEDS + niche_search_feeds(),
            },
            logger=log,
        )
    ]
    # Optional providers stay out of the list entirely when unkeyed, rather than
    # being added and failing every request.
    if settings.news_api_key:
        collectors.append(
            NewsApiCollector(settings.news_api_key.get_secret_value(), logger=log)
        )
    if settings.search_api_key:
        collectors.append(
            WebSearchCollector(settings.search_api_key.get_secret_value(), logger=log)
        )

    ai_client = OpenRouterClient(
        api_key=settings.openrouter_api_key.get_secret_value(),
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
        logger=log,
    )

    # PRD 8: git-backed durability. Absent a token the pipeline runs local-only,
    # which is correct on a host with a persistent disk and for local development --
    # it just means history does not survive a Render redeploy.
    sync = None
    if settings.github_token:
        sync = GitHubSync(
            token=settings.github_token.get_secret_value(),
            repo=settings.github_data_repo,
            branch=settings.github_data_branch,
            data_dir=data_dir,
            logger=log,
        )
    else:
        log.warning(
            "GIT_SYNC_DISABLED reason=no_github_token "
            "detail=history will not survive a redeploy"
        )

    deps = PipelineDeps(
        settings=settings,
        sync=sync,
        repo=Repository(
            LocalCsvBackend(data_dir, logger=log),
            tz=settings.tz,
            retention_days=settings.data_retention_days,
            logger=log,
        ),
        collection=CollectionService(collectors, logger=log),
        ai=AiService(ai_client, logger=log),
        sender=ResendSender(settings.email_api_key.get_secret_value(), logger=log),
        data_dir=data_dir,
    )

    return BriefingRunner(deps, logger=log)
