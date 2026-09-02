# Melbourne Mama — Morning Intelligence

A daily trend intelligence agent. Every morning at **07:30 IST** it collects roughly
1,200 articles from 55 sources, clusters them into stories, ranks them through two
independent engines, researches the top ten with an LLM, and emails a briefing:

- **Global Pulse** — the five biggest stories worldwide, chosen with no knowledge of
  the founder's interests
- **Brand Radar** — up to two stories for each of the ten pages in the portfolio, in
  its own block. A quiet lane says so rather than being hidden
- **Creative Spark** — one creative opportunity drawn from the day's news

Specification: [docs/PRD.md](docs/PRD.md). Build plan and decisions: [PLAN.md](PLAN.md).

---

## How it runs

```
GitHub Actions  ──POST──▶  Render (FastAPI)  ──▶  RSS · News API · Web search
   0 2 * * * UTC                │                      │
   = 07:30 IST                  │                      ▼
                                │              dedup → cluster → rank
                                │                      │
                                │                      ▼
                                │              OpenRouter (Kimi)
                                │                      │
                                │                      ▼
                                ├──────────────▶  Resend → inbox
                                │
                                └──────────────▶  data/*.csv → this repo
```

GitHub Actions only schedules and fires one HTTP request. All processing is on Render.

---

## Local development

```bash
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
cp .env.example .env          # then fill it in
```

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

```bash
.venv/Scripts/python.exe -m pytest        # 487 tests, no network, no API credits
.venv/Scripts/python.exe -m ruff check .
```

Trigger a run (`AGENT_SECRET` from your `.env`):

```bash
curl -X POST -H "Authorization: Bearer $AGENT_SECRET" "http://localhost:8000/api/daily-brief?dry_run=true"
```

`dry_run=true` builds the entire briefing and sends nothing. Use it for everything
except a deliberate delivery.

---

## Configuration

| Variable | Required | Notes |
|---|---|---|
| `AGENT_SECRET` | **to boot** | Bearer token for the endpoint. Minimum 16 characters |
| `OPENROUTER_API_KEY` | to run | |
| `OPENROUTER_MODEL` | | Default `moonshotai/kimi-k2`. Never hard-coded |
| `EMAIL_PROVIDER` | | `sendgrid` (default) or `resend` |
| `EMAIL_API_KEY` | to run | For whichever provider is selected |
| `SENDER_EMAIL` | to run | Must be a verified sender — see below. `Name <a@b.com>` is accepted |
| `RECIPIENT_EMAIL` | to run | Unrestricted once the sender is verified |
| `TIMEZONE` | | `Asia/Kolkata`. See Amendment A1 in PLAN.md |
| `GLOBAL_TOP_N` / `NICHE_TOP_N` | | Default 5 |
| `DATA_RETENTION_DAYS` | | Default 31 |
| `GITHUB_TOKEN` | for durability | Without it, history is wiped on every redeploy |
| `NEWS_API_KEY` / `SEARCH_API_KEY` | optional | RSS alone yields ~1,200 articles |

Only `AGENT_SECRET` is needed to start the process. The rest are checked before a run
begins, so the service can boot and serve `/health` on a half-configured host.

---

## Deploying

### 1. Render

Point Render at this repo; [render.yaml](render.yaml) describes the service. Fill in
every variable marked `sync: false` in the dashboard — none of them are committed.

> Render's free tier sleeps after inactivity and takes ~50s to wake. The workflow warms
> `/health` before requesting the briefing.

### 2. Verify a sender

No email provider will send from an address it cannot attribute to you. There are two
ways to satisfy that, and they differ a lot in effort.

**SendGrid — Single Sender Verification (default, no DNS).**

1. Create a SendGrid account and go to **Settings → Sender Authentication → Verify a
   Single Sender**.
2. Enter the address you want the briefing to come *from*. SendGrid emails it a
   confirmation link; click it.
3. Create an API key under **Settings → API Keys** with **Mail Send** permission and
   set it as `EMAIL_API_KEY`, with `EMAIL_PROVIDER=sendgrid`.
4. Set `SENDER_EMAIL` to that verified address. The recipient is then unrestricted.

This needs no DNS at all. The trade-off: a `from` address on a domain you do not
control (a `gmail.com` address, say) is not DMARC-aligned, so it will sometimes land in
spam. Acceptable for a briefing you send yourself, not for anything wider.

**Resend — domain authentication (better deliverability, needs DNS).**

1. Add a **subdomain** at [resend.com/domains](https://resend.com/domains), e.g.
   `updates.melbournemama.org`. Use a subdomain, not the root: the root already runs
   Google Workspace with its own SPF record, **a domain may only have one SPF record**,
   and a second one silently breaks SPF for all your normal mail.
2. Publish the DKIM and SPF records Resend gives you, then click **Verify**.
3. Set `EMAIL_PROVIDER=resend` and `SENDER_EMAIL` to an address on that subdomain.

If your DNS host appends the root domain automatically, enter `resend._domainkey.updates`
rather than the full name — a doubled record never verifies, and that is the first thing
to check if verification stalls.

Either way, an unverified sender returns `403`, which is treated as non-retryable
because it will fail identically every time.

### 3. GitHub Actions

Add two repository secrets:

| Secret | Value |
|---|---|
| `AGENT_SECRET` | the same value as on Render |
| `RENDER_URL` | e.g. `https://your-service.onrender.com`, no trailing slash |

The schedule is `0 2 * * *` **UTC**. GitHub cron has no timezone option; IST has no
daylight saving, so 02:00 UTC is 07:30 IST every day of the year.

Run it by hand from the Actions tab — **Daily Morning Brief → Run workflow** — with
optional `dry_run` and `force` inputs.

### 4. Git-backed history

Create a fine-grained PAT with **Contents: read and write** on this repository and set
it as `GITHUB_TOKEN` on Render. Each run pulls `data/*.csv` before it starts and commits
changes afterwards, so history survives a redeploy.

Without it the service still works; it just loses the momentum signal each time Render
restarts, because yesterday's scores are gone.

---

## Operating

| Endpoint | |
|---|---|
| `GET /health` | Liveness. Exposes nothing else |
| `POST /api/daily-brief` | Runs the briefing. Requires `Authorization: Bearer $AGENT_SECRET` |
| `?dry_run=true` | Build everything, send nothing |
| `?force=true` | Ignore duplicate protection |

Responses follow PRD §78. **A failed run returns HTTP 500** so the Actions step goes
red — a green tick on a morning with no email would be worse than a red one. Incomplete
configuration returns 503.

### Reading the logs

Each run logs one line per stage, prefixed with its `run_id`:

```
START dry_run=False force=False
COLLECTION_COMPLETED collected=1211 sources_failed=0
DEDUP_COMPLETED section=global before=480 after=454
CLUSTERING_COMPLETED section=global topics=375
GLOBAL_RANKING_COMPLETED ranked=375 selected=5
RESEARCH_COMPLETED succeeded=9 failed=1 spark=True
EMAIL_RENDERED html_bytes=44679 partial=True
END status=completed duration=114.9s
```

### When something goes wrong

| Symptom | Cause |
|---|---|
| `403 ... not verified` | Step 2 above is incomplete for whichever provider is selected |
| `CONFIG_INVALID` (503) | A variable in the table above is missing |
| `RUN_ALREADY_IN_PROGRESS` | Another run holds the lock. Stale locks clear after 15 min |
| `already_completed` | Today's briefing was already delivered. Use `force=true` |
| `NO_USABLE_NEWS` | Every source failed, or fewer than 3 topics survived |
| `AI_PROCESSING_FAILED` | Every topic failed at the model. Check the key and credit |
| `EXECUTION_TIMEOUT` | Run exceeded 10 minutes |
| Momentum always neutral | `GITHUB_TOKEN` missing, so history resets each redeploy |

Secrets are redacted from logs, and an unexpected exception is reported as
`INTERNAL_ERROR` without a traceback in the response.

---

## Design notes

Things that are deliberate and look wrong otherwise:

- **The global ranker cannot see the founder profile.** Not by convention — `rank_global`
  takes no profile argument, and a test parses the import graph to prove it.
- **The model is never asked for a URL.** Sources are cited by index and rebuilt from
  retrieved articles, so a fabricated reference has nowhere to live.
- **`sources.csv` is exempt from the 31-day retention sweep.** It is a registry keyed by
  domain, not a time series; pruning it would reset every source's reputation monthly.
- **`response_format: json_object` is not sent.** Kimi rejects it, and the model must
  stay swappable.
- **Creative Radar excludes zero-relevance topics.** At 10% of the weight, relevance
  alone cannot keep an unrelated story out; without the gate, reality-TV renewals topped
  the section.
- **Analysis is persisted before the email sends.** A run that cannot write its history
  must not deliver a briefing tomorrow's momentum will disagree with.
