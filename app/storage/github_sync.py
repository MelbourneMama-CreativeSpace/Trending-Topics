"""Git-backed durability for the CSV files (PRD 8).

Render's filesystem is ephemeral: a redeploy wipes `data/`, and with it every topic
identity and trend score the momentum calculation depends on. This syncs the directory
to and from the GitHub repository through the Contents API.

**Sync, not a storage backend.** The obvious design is a `StorageBackend` that talks to
GitHub on every read and write, but the pipeline reads and writes those files a dozen
times per run, which would mean a dozen round trips and a dozen commits for one
briefing. Instead the local directory stays the working copy -- using the
`LocalCsvBackend` that Phase 2 already tested -- and the network is touched exactly
twice: pull at the start, push at the end.

That also means a network failure degrades sensibly. A failed pull leaves the run
working from whatever is on disk; a failed push loses one day of history rather than
the briefing.
"""

import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.logging_setup import LOGGER_NAME
from app.storage.datasets import Dataset, filename_for

GITHUB_API = "https://api.github.com"
API_VERSION = "2022-11-28"
DEFAULT_TIMEOUT_SECONDS = 30.0

# GitHub rejects commits above this size through the Contents API. Our files are far
# smaller, but a runaway articles.csv should fail loudly rather than silently.
MAX_FILE_BYTES = 5 * 1024 * 1024


@dataclass
class SyncReport:
    pulled: list[str] = field(default_factory=list)
    pushed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


class GitHubSync:
    """Pulls `data/*.csv` from the repository at start, pushes changes at the end."""

    def __init__(
        self,
        token: str,
        repo: str,
        branch: str = "main",
        data_dir: Path = Path("data"),
        path_prefix: str = "data",
        logger: logging.Logger | None = None,
    ) -> None:
        self._token = token
        self._repo = repo
        self._branch = branch
        self._data_dir = Path(data_dir)
        self._prefix = path_prefix.strip("/")
        self._log = logger or logging.getLogger(LOGGER_NAME)
        # Blob SHAs from the pull, needed to update rather than create on push.
        self._shas: dict[str, str] = {}

    # --- plumbing ------------------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }

    def _url(self, filename: str) -> str:
        return f"{GITHUB_API}/repos/{self._repo}/contents/{self._prefix}/{filename}"

    # --- pull ----------------------------------------------------------------

    async def pull(self, client: httpx.AsyncClient) -> SyncReport:
        """Fetch every dataset into the local working directory.

        A file missing from the repository is normal on a first run and is not an
        error. A network failure is logged and the run continues on local state --
        history improves ranking but is never required to produce a briefing.
        """
        report = SyncReport()
        self._data_dir.mkdir(parents=True, exist_ok=True)

        for dataset in Dataset:
            filename = filename_for(dataset)
            try:
                response = await client.get(
                    self._url(filename),
                    headers=self._headers,
                    params={"ref": self._branch},
                )
            except httpx.HTTPError as exc:
                self._log.warning("GITHUB_PULL_FAILED file=%s error=%s", filename, exc)
                report.failed.append(filename)
                continue

            if response.status_code == 404:
                report.unchanged.append(filename)
                continue

            if response.status_code != 200:
                self._log.warning(
                    "GITHUB_PULL_FAILED file=%s status=%d", filename, response.status_code
                )
                report.failed.append(filename)
                continue

            payload = response.json()
            self._shas[filename] = payload.get("sha", "")
            content = base64.b64decode(payload.get("content", "")).decode(
                "utf-8", errors="replace"
            )
            (self._data_dir / filename).write_text(content, encoding="utf-8", newline="")
            report.pulled.append(filename)

        self._log.info(
            "GITHUB_PULL pulled=%d absent=%d failed=%d",
            len(report.pulled), len(report.unchanged), len(report.failed),
        )
        return report

    # --- push ----------------------------------------------------------------

    async def push(self, client: httpx.AsyncClient, message: str) -> SyncReport:
        """Commit changed datasets back to the repository."""
        report = SyncReport()

        for dataset in Dataset:
            filename = filename_for(dataset)
            path = self._data_dir / filename
            if not path.exists():
                continue

            raw = path.read_bytes()
            if len(raw) > MAX_FILE_BYTES:
                self._log.error(
                    "GITHUB_PUSH_TOO_LARGE file=%s bytes=%d", filename, len(raw)
                )
                report.failed.append(filename)
                continue

            if await self._put(client, filename, raw, message, report):
                report.pushed.append(filename)

        self._log.info(
            "GITHUB_PUSH pushed=%d unchanged=%d failed=%d",
            len(report.pushed), len(report.unchanged), len(report.failed),
        )
        return report

    async def _put(
        self,
        client: httpx.AsyncClient,
        filename: str,
        raw: bytes,
        message: str,
        report: SyncReport,
    ) -> bool:
        body: dict[str, object] = {
            "message": message,
            "content": base64.b64encode(raw).decode("ascii"),
            "branch": self._branch,
        }
        sha = self._shas.get(filename)
        if sha:
            body["sha"] = sha

        try:
            response = await client.put(
                self._url(filename), headers=self._headers, json=body
            )
        except httpx.HTTPError as exc:
            self._log.warning("GITHUB_PUSH_FAILED file=%s error=%s", filename, exc)
            report.failed.append(filename)
            return False

        if response.status_code in (200, 201):
            self._shas[filename] = response.json().get("content", {}).get("sha", "")
            return True

        if response.status_code == 409:
            # Someone else committed since our pull. One retry with a fresh SHA is
            # enough for a once-daily job; a second conflict means something else is
            # writing and we should not fight it.
            self._log.warning("GITHUB_PUSH_CONFLICT file=%s, retrying with fresh sha", filename)
            if await self._refresh_sha(client, filename):
                return await self._put(client, filename, raw, message, SyncReport())
            report.failed.append(filename)
            return False

        if response.status_code == 422 and not sha:
            # 422 without a SHA means the file already exists. Fetch its SHA and update.
            if await self._refresh_sha(client, filename):
                return await self._put(client, filename, raw, message, SyncReport())

        self._log.error(
            "GITHUB_PUSH_FAILED file=%s status=%d body=%s",
            filename, response.status_code, " ".join(response.text[:200].split()),
        )
        report.failed.append(filename)
        return False

    async def _refresh_sha(self, client: httpx.AsyncClient, filename: str) -> bool:
        try:
            response = await client.get(
                self._url(filename), headers=self._headers, params={"ref": self._branch}
            )
        except httpx.HTTPError:
            return False

        if response.status_code != 200:
            return False

        self._shas[filename] = response.json().get("sha", "")
        return bool(self._shas[filename])


def build_github_client(timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))
