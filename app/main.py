"""FastAPI application (PRD 49, 50, 51, 78).

Two endpoints. `/health` is a liveness probe that exposes nothing; the briefing
endpoint authenticates, runs the PRD 79 pipeline, and reports what happened.

The runner arrives through a dependency rather than being constructed inline, so tests
can substitute one and exercise the endpoint's contract without a network.
"""

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.config import Settings, get_settings
from app.deps import build_runner
from app.errors import BriefingError
from app.logging_setup import configure_logging
from app.pipeline import BriefingRunner
from app.security import require_agent_secret


def get_runner(
    settings: Annotated[Settings, Depends(get_settings)],
) -> BriefingRunner:
    """The production pipeline. Raises `BriefingError` when config is incomplete."""
    return build_runner(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate config once, at boot, and wire secret redaction into the logger.

    A missing or weak `AGENT_SECRET` raises here and the process refuses to start --
    far better than serving an unauthenticated endpoint.
    """
    settings = get_settings()
    configure_logging(secret_values=settings.secret_values())
    yield


async def _briefing_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Configuration problems are 503, not 500: the service is not ready, not broken."""
    code = exc.code.value if isinstance(exc, BriefingError) else "INTERNAL_ERROR"
    return JSONResponse(
        status_code=503, content={"success": False, "status": "failed", "error": code}
    )


def create_app() -> FastAPI:
    """App factory, so tests can build an isolated instance per environment."""
    app = FastAPI(
        title="Melbourne Mama Morning Intelligence",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_exception_handler(BriefingError, _briefing_error_handler)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe (PRD 51). Deliberately exposes nothing but liveness."""
        return {"status": "ok"}

    @app.post("/api/daily-brief", dependencies=[Depends(require_agent_secret)])
    async def daily_brief(
        runner: Annotated[BriefingRunner, Depends(get_runner)],
        dry_run: Annotated[
            bool, Query(description="Build the briefing but do not send it (PRD 86).")
        ] = False,
        force: Annotated[
            bool, Query(description="Ignore duplicate-briefing protection (PRD 49).")
        ] = False,
    ) -> JSONResponse:
        """Run today's briefing.

        Reaching this point means the request was authenticated, so `force` is already
        restricted to authenticated callers per PRD 49.

        A failed run returns HTTP 500 with its error code, so the GitHub Actions step
        fails visibly rather than reporting a green tick on a morning with no email.
        """
        outcome = await runner.run(dry_run=dry_run, force=force)

        return JSONResponse(
            status_code=200 if outcome.success else 500,
            content=outcome.to_response(),
        )

    return app


app = create_app()
