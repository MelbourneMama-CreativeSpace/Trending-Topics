"""Phase 9: git-backed persistence and deployment configuration (PRD 8, 52, 76, 91)."""

import base64
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
import yaml

from app.storage import Dataset
from app.storage.github_sync import GitHubSync, build_github_client

REPO = "MelbourneMama-CreativeSpace/Trending-Topics"
CONTENTS = f"https://api.github.com/repos/{REPO}/contents/data"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def sync_for(tmp_path) -> GitHubSync:
    return GitHubSync(token="ghp_test", repo=REPO, branch="main", data_dir=tmp_path / "data")


def content_response(text: str, sha: str = "abc123") -> httpx.Response:
    return httpx.Response(
        200,
        json={"content": base64.b64encode(text.encode()).decode(), "sha": sha,
              "encoding": "base64"},
    )


# --- pull (PRD 8) ------------------------------------------------------------


@pytest.mark.integration
@respx.mock
async def test_pull_restores_files_into_the_working_directory(tmp_path):
    respx.get(f"{CONTENTS}/articles.csv").mock(
        return_value=content_response("id,title\n1,Restored\n")
    )
    respx.get(url__regex=rf"{CONTENTS}/.*").mock(return_value=httpx.Response(404))
    sync = sync_for(tmp_path)

    async with build_github_client() as client:
        report = await sync.pull(client)

    assert "articles.csv" in report.pulled
    assert (tmp_path / "data" / "articles.csv").read_text(encoding="utf-8") == (
        "id,title\n1,Restored\n"
    )


@pytest.mark.integration
@respx.mock
async def test_a_missing_file_is_normal_on_a_first_run(tmp_path):
    respx.get(url__regex=rf"{CONTENTS}/.*").mock(return_value=httpx.Response(404))
    sync = sync_for(tmp_path)

    async with build_github_client() as client:
        report = await sync.pull(client)

    assert report.ok is True
    assert report.pulled == []


@pytest.mark.integration
@respx.mock
async def test_a_pull_failure_does_not_raise(tmp_path):
    """History improves ranking; it is never required to produce a briefing."""
    respx.get(url__regex=rf"{CONTENTS}/.*").mock(side_effect=httpx.ConnectError("down"))
    sync = sync_for(tmp_path)

    async with build_github_client() as client:
        report = await sync.pull(client)

    assert report.ok is False
    assert len(report.failed) == len(list(Dataset))


@pytest.mark.integration
@respx.mock
async def test_pull_survives_a_redeploy(tmp_path):
    """The whole point of PRD 8: an empty local disk is repopulated from the repo."""
    respx.get(f"{CONTENTS}/topics.csv").mock(
        return_value=content_response("topic_id,headline\nt1,Yesterday\n")
    )
    respx.get(url__regex=rf"{CONTENTS}/.*").mock(return_value=httpx.Response(404))
    data_dir = tmp_path / "data"
    assert not data_dir.exists(), "premise: the disk starts empty, as after a redeploy"

    async with build_github_client() as client:
        await sync_for(tmp_path).pull(client)

    assert "Yesterday" in (data_dir / "topics.csv").read_text(encoding="utf-8")


# --- push --------------------------------------------------------------------


@pytest.mark.integration
@respx.mock
async def test_push_creates_a_file_that_does_not_exist_yet(tmp_path):
    # Written as bytes on purpose. csv.writer emits CRLF, and write_text would let
    # Windows rewrite the line endings, so the test would not match what push sends.
    payload = b"id,title\r\n1,New\r\n"
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "data" / "articles.csv").write_bytes(payload)
    route = respx.put(f"{CONTENTS}/articles.csv").mock(
        return_value=httpx.Response(201, json={"content": {"sha": "new123"}})
    )

    async with build_github_client() as client:
        report = await sync_for(tmp_path).push(client, "data: test")

    assert "articles.csv" in report.pushed
    body = route.calls[0].request.read().decode()
    assert base64.b64encode(payload).decode() in body, "push must be byte-exact"
    assert '"sha"' not in body, "a new file must not send a sha"


@pytest.mark.integration
@respx.mock
async def test_push_updates_an_existing_file_using_its_sha(tmp_path):
    """The Contents API rejects an update that omits the current blob SHA."""
    respx.get(f"{CONTENTS}/topics.csv").mock(
        return_value=content_response("topic_id\nold\n", sha="sha-from-pull")
    )
    respx.get(url__regex=rf"{CONTENTS}/.*").mock(return_value=httpx.Response(404))
    put = respx.put(f"{CONTENTS}/topics.csv").mock(
        return_value=httpx.Response(200, json={"content": {"sha": "sha-after-push"}})
    )
    sync = sync_for(tmp_path)

    async with build_github_client() as client:
        await sync.pull(client)
        (tmp_path / "data" / "topics.csv").write_text("topic_id\nnew\n", encoding="utf-8")
        await sync.push(client, "data: test")

    assert "sha-from-pull" in put.calls[0].request.read().decode()


@pytest.mark.integration
@respx.mock
async def test_a_conflicting_push_is_retried_with_a_fresh_sha(tmp_path):
    """Another writer committed since our pull. One retry is right for a daily job."""
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "data" / "articles.csv").write_text("id\n1\n", encoding="utf-8")
    put = respx.put(f"{CONTENTS}/articles.csv").mock(
        side_effect=[
            httpx.Response(409, json={"message": "conflict"}),
            httpx.Response(200, json={"content": {"sha": "after"}}),
        ]
    )
    respx.get(f"{CONTENTS}/articles.csv").mock(
        return_value=content_response("id\nremote\n", sha="fresh-sha")
    )

    async with build_github_client() as client:
        report = await sync_for(tmp_path).push(client, "data: test")

    assert put.call_count == 2
    assert "fresh-sha" in put.calls[1].request.read().decode()
    assert "articles.csv" in report.pushed


@pytest.mark.integration
@respx.mock
async def test_a_push_failure_is_reported_not_raised(tmp_path):
    """Losing one day of history is bad; losing the briefing to a GitHub outage is worse."""
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "data" / "articles.csv").write_text("id\n1\n", encoding="utf-8")
    respx.put(url__regex=rf"{CONTENTS}/.*").mock(return_value=httpx.Response(500))

    async with build_github_client() as client:
        report = await sync_for(tmp_path).push(client, "data: test")

    assert report.ok is False
    assert "articles.csv" in report.failed


@pytest.mark.integration
@respx.mock
async def test_an_oversized_file_is_refused(tmp_path):
    from app.storage.github_sync import MAX_FILE_BYTES

    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "data" / "articles.csv").write_bytes(b"x" * (MAX_FILE_BYTES + 1))

    async with build_github_client() as client:
        report = await sync_for(tmp_path).push(client, "data: test")

    assert "articles.csv" in report.failed


@pytest.mark.integration
@respx.mock
async def test_files_absent_locally_are_not_pushed(tmp_path):
    (tmp_path / "data").mkdir(parents=True)
    put = respx.put(url__regex=rf"{CONTENTS}/.*").mock(
        return_value=httpx.Response(200, json={"content": {"sha": "s"}})
    )

    async with build_github_client() as client:
        await sync_for(tmp_path).push(client, "data: test")

    assert put.call_count == 0


@pytest.mark.integration
@respx.mock
async def test_the_token_is_sent_as_a_bearer_header(tmp_path):
    respx.get(url__regex=rf"{CONTENTS}/.*").mock(return_value=httpx.Response(404))

    async with build_github_client() as client:
        await sync_for(tmp_path).pull(client)

    assert respx.calls[0].request.headers["Authorization"] == "Bearer ghp_test"


# --- pipeline integration ----------------------------------------------------


@pytest.mark.integration
async def test_a_run_without_a_token_is_local_only():
    """No GITHUB_TOKEN means no sync object, and the pipeline simply skips it."""
    from app.pipeline import PipelineDeps

    assert "sync" in PipelineDeps.__dataclass_fields__
    assert PipelineDeps.__dataclass_fields__["sync"].default is None


# --- deployment configuration (PRD 52, 76) -----------------------------------


@pytest.mark.unit
def test_workflow_cron_fires_at_0730_ist():
    """GitHub cron is UTC. IST has no daylight saving, so 02:00 UTC is 07:30 IST
    every day of the year -- which is why this is a constant and not a workaround."""
    workflow = yaml.safe_load(
        (PROJECT_ROOT / ".github/workflows/daily-brief.yml").read_text(encoding="utf-8")
    )
    # PyYAML parses the bare `on:` key as the boolean True.
    schedule = workflow[True]["schedule"]
    assert schedule[0]["cron"] == "0 2 * * *"

    ist = ZoneInfo("Asia/Kolkata")
    fired = dt.datetime(2026, 9, 1, 2, 0, tzinfo=ZoneInfo("UTC")).astimezone(ist)
    assert (fired.hour, fired.minute) == (7, 30)

    # And in January, when a DST-observing zone would have drifted an hour.
    winter = dt.datetime(2026, 1, 15, 2, 0, tzinfo=ZoneInfo("UTC")).astimezone(ist)
    assert (winter.hour, winter.minute) == (7, 30)


@pytest.mark.unit
def test_workflow_only_schedules_and_never_processes():
    """PRD 53: GitHub schedules; Render does the work."""
    raw = (PROJECT_ROOT / ".github/workflows/daily-brief.yml").read_text(encoding="utf-8")
    # Comments describe what the workflow deliberately does NOT do, so checking the
    # raw text would match the very words it promises to avoid.
    directives = " ".join(
        line for line in raw.splitlines() if not line.strip().startswith("#")
    ).lower()

    for forbidden in ("pip install", "python ", "openrouter", "resend", "actions/checkout"):
        assert forbidden not in directives, f"workflow should not do {forbidden!r}"


@pytest.mark.unit
def test_workflow_fails_the_step_when_the_run_fails():
    """Without --fail-with-body a morning with no email shows a green tick."""
    raw = (PROJECT_ROOT / ".github/workflows/daily-brief.yml").read_text(encoding="utf-8")

    assert "--fail-with-body" in raw


@pytest.mark.unit
def test_workflow_supports_manual_dispatch():
    """PRD 87: manual execution for testing."""
    workflow = yaml.safe_load(
        (PROJECT_ROOT / ".github/workflows/daily-brief.yml").read_text(encoding="utf-8")
    )

    assert "workflow_dispatch" in workflow[True]


@pytest.mark.unit
def test_workflow_commits_no_secrets():
    """PRD 76: secrets come from the GitHub secret store, never the file."""
    raw = (PROJECT_ROOT / ".github/workflows/daily-brief.yml").read_text(encoding="utf-8")

    assert "secrets.AGENT_SECRET" in raw
    assert "sk-or-" not in raw
    assert "re_" not in raw.replace("required", "").replace("re_delay", "")


@pytest.mark.unit
def test_render_blueprint_marks_every_secret_as_unsynced():
    """PRD 76: `sync: false` makes Render prompt for the value instead of reading
    it from a committed file."""
    blueprint = yaml.safe_load((PROJECT_ROOT / "render.yaml").read_text(encoding="utf-8"))
    env_vars = {v["key"]: v for v in blueprint["services"][0]["envVars"]}

    for secret in ("AGENT_SECRET", "OPENROUTER_API_KEY", "EMAIL_API_KEY", "GITHUB_TOKEN"):
        assert env_vars[secret].get("sync") is False, f"{secret} must not be committed"
        assert "value" not in env_vars[secret]


@pytest.mark.unit
def test_render_blueprint_uses_ist_and_the_health_check():
    blueprint = yaml.safe_load((PROJECT_ROOT / "render.yaml").read_text(encoding="utf-8"))
    service = blueprint["services"][0]
    env_vars = {v["key"]: v.get("value") for v in service["envVars"]}

    assert service["healthCheckPath"] == "/health"
    assert env_vars["TIMEZONE"] == "Asia/Kolkata"
    assert env_vars["PYTHON_VERSION"].startswith("3.12")


@pytest.mark.unit
def test_no_env_file_is_tracked_by_git():
    """PRD 76, checked as a test rather than trusted to habit."""
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "\n.env\n" in gitignore
    assert not (PROJECT_ROOT / ".env").exists() or ".env" in gitignore


@pytest.mark.unit
def test_data_csvs_are_not_gitignored():
    """Git-backed persistence depends on these being committable (PRD 8)."""
    raw = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    patterns = {
        line.strip() for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert "data/*.csv" not in patterns, "git-backed persistence needs these committable"
    assert "data/" not in patterns
    assert "data/*.lock" in patterns, "the lock is ephemeral and must stay uncommitted"
