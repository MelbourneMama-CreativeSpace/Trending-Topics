"""FastAPI application (PRD 51, 49, 50).

Phase 1 exposes the two endpoints and the whole request envelope -- auth, run_id,
mode parsing, structured logging. The pipeline itself lands in Phase 8; until then
`/api/daily-brief` accepts a request, logs it, and reports that the pipeline is not
yet implemented rather than pretending to have sent anything.
"""

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Query

from app import __version__
from app.config import Settings, get_settings
from app.logging_setup import LogEvent, RunLogger, configure_logging
from app.run_context import generate_run_id
from app.security import require_agent_secret


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate config once, at boot, and wire secret redaction into the logger.

    A missing or weak `AGENT_SECRET` raises here and the process refuses to start --
    far better than serving an unauthenticated endpoint.
    """
    settings = get_settings()
    configure_logging(secret_values=settings.secret_values())
    yield


def create_app() -> FastAPI:
    """App factory, so tests can build an isolated instance per environment."""
    app = FastAPI(
        title="Melbourne Mama Morning Intelligence",
        version=__version__,
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe (PRD 51). Deliberately exposes nothing but liveness."""
        return {"status": "ok"}

    @app.post("/api/daily-brief", dependencies=[Depends(require_agent_secret)])
    async def daily_brief(
        settings: Annotated[Settings, Depends(get_settings)],
        dry_run: Annotated[
            bool, Query(description="Build the briefing but do not send it (PRD 86).")
        ] = False,
        force: Annotated[
            bool, Query(description="Ignore duplicate-briefing protection (PRD 49).")
        ] = False,
    ) -> dict[str, object]:
        """Run today's briefing.

        Reaching this point means the request was authenticated -- `force` is therefore
        already restricted to authenticated callers, per PRD 49.
        """
        run_id = generate_run_id(settings.tz)
        log = RunLogger(run_id)
        log.event(LogEvent.START, dry_run=dry_run, force=force)
        log.event(LogEvent.AUTHENTICATED)

        missing = settings.missing_pipeline_config()

        # Phase 1 stub. Phase 8 replaces this with the PRD 79 sequence.
        log.event(LogEvent.SKIPPED, reason="pipeline_not_implemented")
        log.event(LogEvent.END)

        return {
            "run_id": run_id,
            "status": "accepted",
            "pipeline": "not_implemented",
            "phase": 1,
            "dry_run": dry_run,
            "force": force,
            "timezone": settings.timezone,
            "missing_pipeline_config": missing,
        }

    return app


app = create_app()
