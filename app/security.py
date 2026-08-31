"""Bearer-token auth for the briefing endpoint (PRD 50).

Every rejection returns the same opaque 401. The response must not reveal whether
`AGENT_SECRET` is configured, what it looks like, or how the supplied token differed --
otherwise the endpoint becomes an oracle.
"""

import secrets as stdlib_secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

# auto_error=False so a missing or malformed header reaches us as `None` and gets the
# same generic 401 as a wrong token, rather than FastAPI's more descriptive 403.
_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_agent_secret(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> None:
    """Reject anything that is not exactly the configured bearer token.

    Comparison is constant-time: a plain `==` leaks the shared prefix length through
    timing, which is enough to recover a secret given enough attempts.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()

    expected = settings.agent_secret.get_secret_value()
    if not stdlib_secrets.compare_digest(credentials.credentials, expected):
        raise _unauthorized()
