# Implementation Plan — Melbourne Mama Morning Trend Intelligence

Companion to [PRD.md](docs/PRD.md). Ten phases, each with its own tests and a hard exit
criterion. No phase starts until the previous one's exit criteria are green.

## Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| Python | 3.12.10 | PRD requires 3.12+; best wheel coverage; Render supports it |
| **Timezone** | **`Asia/Kolkata` (IST, UTC+5:30)** | **Amendment A1 — supersedes the PRD's `Australia/Melbourne`** |
| Send time | 07:30 IST = **02:00 UTC**, fixed all year | IST has no daylight saving |
| Email provider | **Resend** | Simplest API, free tier covers a daily send |
| Persistence | **Git-backed** to this repo | Free, durable across Render redeploys, history visible in GitHub |
| Source discovery | **RSS + News API + Web Search**, reliability-weighted | Per founder's source-quality architecture |
| AI gateway | OpenRouter, model from `OPENROUTER_MODEL` | Never hard-code the model |

### Amendment A1 — operating timezone is IST

The PRD specifies `Australia/Melbourne` throughout. The operating timezone is **IST**.
This governs the schedule, `briefing_id` (`2026-09-01-Asia/Kolkata`), the 31-day retention
cutoff, and the email header. The PRD body is preserved as written; this table is authoritative.

Two consequences, both simplifications:

- **No DST.** Melbourne shifts between UTC+10 and UTC+11; IST never moves. The GitHub Actions
  cron is a constant `0 2 * * *` with no seasonal correction.
- **The PRD's Section 52 YAML has a latent bug regardless:** GitHub Actions cron has no
  `timezone:` key — schedules are always UTC. Under IST that stops mattering.

## Source architecture (drives Phases 3 and 5)

```text
                    SOURCE DISCOVERY
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
       RSS             News API           Web Search
        │                  │                  │
        └──────────────────┼──────────────────┘
                           ▼
                    SOURCE QUALITY
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
       High reliability          Low reliability
              │                         │
              ▼                         ▼
        Strong weight              Low weight
```

Every collected article carries its `source_domain`. `sources.csv` holds a
`reliability_score` per domain, which weights that article's contribution to a topic's
`source_breadth_score`. Ten low-reliability aggregators must not outrank two wire services.

---

## Phase 0 — Environment & scaffold ✅

**Goal:** a venv that runs, dependencies pinned, package layout in place, `pytest` green.

Deliverables: `.venv` (Python 3.12.10) · `requirements.txt` · `requirements-dev.txt` ·
`app/` package skeleton · `tests/` · `pyproject.toml` (pytest + ruff config) · `.env.example`

**Tests:** import smoke test · `ZoneInfo("Asia/Kolkata")` resolves · IST offset fixed year-round ·
07:30 IST maps to 02:00 UTC · `pytest` collects and passes · `ruff check` clean

**Exit criteria:** `pytest` exits 0 · `ruff check` exits 0 · all runtime imports succeed — **met**

---

## Phase 1 — Config, API skeleton, auth, logging ✅

**Goal:** the two endpoints exist and are correctly protected.

Deliverables: `GET /health` · `POST /api/daily-brief` · `Settings` via pydantic-settings
(all PRD §55 env vars, `TIMEZONE` defaulting to `Asia/Kolkata`) · bearer-token auth against
`AGENT_SECRET` · `run_id` generation (`20260901-073000-a81f`) · structured logger with the
PRD §74 event vocabulary · `ErrorCode` enum (PRD §56) · `dry_run` / `force` query params parsed

**Tests:** `test_authentication` (missing / malformed / wrong / correct token → 401/401/401/200) ·
auth failure leaks nothing about the secret · `/health` returns `{"status":"ok"}` and exposes no
config · settings load from env with correct defaults · missing required env fails loudly at startup ·
`run_id` format and uniqueness · `run_id` timestamp rendered in IST

**Exit criteria:** uvicorn boots · `/health` 200 · `/api/daily-brief` 401 without token, 200 with ·
no secret appears in any log line or response body — **met** (verified against a live uvicorn
process, not only TestClient)

---

## Phase 2 — CSV persistence, retention, atomic writes, locking ✅

**Goal:** durable, crash-safe, self-pruning storage behind a swappable interface.

Deliverables: five schemas (articles, topics, trend_scores, briefings, sources) as typed models ·
`StorageBackend` interface with a `LocalCsvBackend` · atomic write (tmp → validate → replace) ·
`cleanup_old_data()` with 31-day cutoff **in IST** · corruption recovery (preserve, copy,
continue) · execution lock → `RUN_ALREADY_IN_PROGRESS` · briefing idempotency by
`briefing_id = date + timezone`

**Tests:** `test_csv_atomic_write` — crash mid-write leaves the original intact · `test_csv_cleanup`
(freezegun: 30-day row survives, 32-day row deleted, file itself survives, headers preserved) ·
**retention boundary computed in IST, not UTC** (a row near midnight IST must not be judged by the
UTC date) · `test_idempotency` — completed briefing returns `already_completed`, no second send ·
corrupt CSV → `CSV_READ_FAILED`, file preserved, pipeline continues · concurrent run rejected

**Exit criteria:** kill -9 simulation during write never corrupts a CSV · retention exact at the
31-day boundary · second same-day run does not re-send — **met**

> **Decision:** `sources.csv` is exempt from the 31-day cutoff. It is a registry keyed by domain,
> not a time series: `reliability_score` is accumulated evidence that Phase 5 ranking depends on,
> and pruning it monthly would reset every source's reputation and flatten source-quality
> weighting. Its size is bounded by distinct domains, not by time.

---

## Phase 3 — Source discovery & source quality ✅

**Goal:** collect from three channels independently, never let one failure kill the run.

Deliverables: `Collector` interface · `RssCollector` (global wires + Telugu/Tollywood/trade feeds +
Google Trends daily RSS, no key needed) · `NewsApiCollector` · `WebSearchCollector` (English +
Telugu queries per PRD §21) · separate global and niche feed registries with **no niche bias in the
global set** · per-request timeouts (10–20s) · retry only on transient codes (429/5xx/timeout) ·
source registry with `reliability_score` · `sources.csv` success/failure tracking · article
normalization + `content_hash` · URL validation (http/https only; reject `javascript:`/`file:`/`data:`/localhost)

**Tests:** `test_url_validation` (scheme allowlist, malformed, injection schemes) ·
`test_article_normalization` (tracking-param stripping, date parsing across feed formats, missing
fields) · one feed 500s → others still collect · all providers fail → `NO_USABLE_NEWS` · timeout
enforced · reliability weighting applied · Telugu (non-ASCII) queries survive round-trip · retry
fires on 429, does **not** fire on 401/404

**Exit criteria:** ≥30 global and ≥10 niche articles from RSS alone with zero API keys ·
no single source failure aborts collection — **met**: live run collected **480 global / 731 niche**
across 55 feeds with **0 failures** in 13.8s, including **200 Telugu-language** articles.

> **Note for Phase 4:** collection yields ~1,200 articles per run. PRD §83–84 require filtering
> down to ~40 topics before any AI call, so dedup and clustering carry real cost-control weight,
> not just tidiness.

---

## Phase 4 — Normalization, deduplication, clustering ✅

**Goal:** many articles become few real topics.

Deliverables: 3-level dedup (URL normalization → title similarity via rapidfuzz → keyword/semantic
overlap) · clustering so "OpenAI launches / announces / new model" becomes **one** topic ·
topic first_seen / last_seen tracking across days

**Tests:** `test_duplicate_detection` (same story different URLs and trackers, syndicated wire copy,
near-identical headlines) · `test_topic_clustering` (the PRD §25 OpenAI case → exactly 1 topic;
genuinely different stories stay separate) · no over-merging of distinct same-entity stories ·
cluster survives across runs via stable `topic_id`

**Exit criteria:** PRD §25 case collapses to one topic · distinct stories never merged ·
dedup measurably reduces the corpus on live data — **met**: live corpus 480→382 global topics
(20% fewer) and 731→498 niche (32% fewer), 100% topic carry-over between runs, no cluster
over 25 articles.

> **Calibration finding:** document frequency cannot separate function words from entities at
> this corpus size — in 454 articles, `out` and `court` each appeared in exactly 10. Clustering
> quality therefore rests on a comprehensive stopword list, not on IDF rarity alone. Two live
> over-merges (`"running out"`, `"man who"`) are locked in as regression tests.

> **Note for Phase 5:** ranking sees ~380 global and ~500 niche topics. PRD §84's cost control
> holds because AI only ever touches the top 10 after ranking.

---

## Phase 5 — Ranking (global + niche, fully separate) ✅

**Goal:** measurable signals pick the topics — not the model.

Deliverables: global scorer 30/25/20/15/10 (recency, breadth, velocity, engagement, significance) ·
niche scorer 25/20/20/15/10/10 (+ industry importance, niche relevance) · reliability-weighted
breadth · momentum from `trend_scores.csv` history (yesterday 40 → today 82 = +42 velocity) ·
normalize 0–100 · `rank_global()` and `rank_niche()` as separate call paths

**Tests:** `test_global_ranking` · `test_niche_ranking` · `test_score_calculation` (weights sum to 1,
output bounded 0–100, each sub-score isolated) · **contamination test — founder profile is not
reachable from the global ranker** · momentum: rising topic outranks equally-popular flat topic ·
missing history degrades to zero velocity, no crash · high-reliability pair outranks low-reliability crowd

**Exit criteria:** global and niche selections provably independent · scores reproducible on fixed
input · absent history never crashes ranking — **met**. Independence is enforced structurally: the
contamination test AST-parses the global ranker's whole import path and asserts `rank_global` has no
profile parameter. Verified it fails when contamination is deliberately injected.

> **Deviation for review — Creative Radar eligibility gate.** PRD §22 weights are applied unchanged
> (relevance stays 10%), but `select_niche_top` additionally excludes topics with zero founder
> relevance. A live run put *"Love Island USA Returns for Season 8 Reunion"* at the top of Creative
> Radar on trade-press coverage and recency alone. At 10% of the weight, relevance cannot keep an
> unrelated story out. PRD §2 defines this section as the five most *relevant* topics, so relevance
> is treated as section membership, not just a ranking signal. Ranking still scores every topic and
> `trend_scores.csv` still records them — only selection is gated.

> **Honesty note:** recency, breadth and velocity are measured. **Engagement and significance are
> proxies** (outlet count, Google Trends presence, source quality, cross-desk spread) — this system
> has no social APIs. The distinction is documented in `app/rank/signals.py` rather than buried.

---

## Phase 6 — AI layer (OpenRouter / Kimi) ✅

**Goal:** research, summarize, personalize — with zero fabricated sources.

Deliverables: OpenRouter client, model from env · retry 3 attempts on transient only, with backoff ·
bounded concurrency (max 3) · research → structured JSON (PRD §33) · summarization ·
personalization (honest low relevance allowed) · Creative Spark (omitted if weak) ·
**prompt-injection hardening** — retrieved content wrapped as untrusted data · strict output
validation · **every returned source URL must match a URL actually retrieved** · conflict detection

**Tests:** `test_ai_json_validation` (malformed JSON → one retry → topic marked failed) ·
**fabricated-URL rejection — model invents a source, it is stripped** · confidence outside 0–1 rejected ·
empty headline/summary rejected · one topic fails, other four continue · all fail →
`AI_PROCESSING_FAILED` · retry on 429 but not on 401 · injected "ignore your instructions" text in a
mock article does not alter behaviour · concurrency cap respected. All mocked via respx — **zero API
credits spent in tests.**

**Exit criteria:** no source reaches output that wasn't retrieved · single-topic failure never aborts
the run · injection attempt provably ineffective — **met**. Live run: 10/10 topics researched,
**35 cited URLs, 0 fabricated**, Creative Spark produced, 60s for 11 calls.

> **Model constraint found live:** `moonshotai/kimi-k2` rejects `response_format: json_object`
> ("does not support feature: structured-outputs"). The parameter is not sent at all — PRD §28
> requires the model to stay swappable, so support cannot be assumed for whatever is configured
> next. JSON is enforced by prompt plus defensive parsing, which works with any model.

> **Notes for Phase 7:**
> - Niche source URLs are `news.google.com/rss/articles/CBMi...` redirects. They resolve correctly
>   and were genuinely retrieved, but read poorly in an email. Resolving them is cheap for the ~40
>   selected sources; it would be 1,200 requests at collection time.
> - Google Trends entries carry `ht_approx_traffic` (e.g. "2000+") — real search volume, which
>   would strengthen the Phase 5 engagement proxy if wired in later.

---

## Phase 7 — Email rendering & sending ✅

**Goal:** a premium, safe, client-compatible email.

Deliverables: Jinja2 HTML (table layout, inline CSS, responsive, no JS) · Global Pulse + Creative
Radar + Creative Spark · header dated in IST · plain-text fallback · **HTML escaping of all model
and web text** · partial-count line ("4 verified niche trends were available today") · Resend
adapter behind an `EmailSender` interface · 3-attempt retry → `email_status=failed`

**Tests:** `test_email_rendering` (5+5, and 5+4 partial) · **XSS — `<script>` in a headline is escaped,
never executed** · only validated URLs become links · plain-text fallback present and non-empty ·
subject line correct · header date renders in IST · send retries 3× then reports failure ·
mocked Resend, no real send

**Exit criteria:** no unescaped model output in HTML · partial state renders honestly ·
renders correctly in a desktop and mobile client — **met** for the first two, verified by rendering
a live briefing: 0 script tags, 0 images, escaping tests confirmed to fail when autoescape is
disabled, and a real 4-of-5 day rendered its shortfall note rather than padding. Client rendering
is confirmed at Phase 9 with a real send.

> **Sender constraint.** Resend requires DNS-verified domains, so a Gmail address can never be a
> sender. `SENDER_EMAIL=collabs@melbournemama.org`; **`melbournemama.org` must be verified at
> resend.com/domains** before a real send works — until then Resend returns 403, which the sender
> correctly treats as non-retryable. The recipient is unconstrained.

> **Google News links are not resolved.** Their `news.google.com/rss/articles/CBMi...` URLs return
> HTTP 200 with a *JavaScript* redirect, not a 3xx, so server-side resolution would mean scraping
> JS. The links work in a browser and each card shows the real publisher name, so this is left
> alone rather than made fragile.

---

## Phase 8 — Orchestration

**Goal:** the 27-step pipeline, wired and mode-aware.

Deliverables: full PRD §79 sequence · `normal` / `dry_run` / `force` modes · execution timeout →
`EXECUTION_TIMEOUT` · minimum-topic thresholds (≥3 each, never fabricate) · partial briefings ·
the three API response shapes (PRD §78) · full run logging

**Tests:** integration News→Ranking→Research→AI→Email→Provider, all mocked ·
`dry_run` generates everything and **sends nothing** · `force` overrides duplicate protection ·
duplicate without force → `already_completed` · 2 global topics → refuse, don't fabricate ·
timeout aborts cleanly and releases the lock · CSV write failure aborts *before* success is recorded ·
lock released on every exit path including exceptions

**Exit criteria:** end-to-end dry run under 5 minutes · every PRD §90 failure row exercised ·
lock never orphaned

---

## Phase 9 — Deployment

**Goal:** it runs itself at 7:30 AM IST.

Deliverables: git-backed storage backend (commits `data/*.csv` via GitHub API) · `render.yaml` ·
GitHub Actions schedule-only workflow (`cron: "0 2 * * *"` UTC = 07:30 IST) · secrets documented ·
README runbook · PRD §91 security + §92 production checklists completed

**Tests:** git-backed backend round-trip (write → commit → re-read after simulated redeploy) ·
cron fires at 07:30 IST · live `/health` · live authenticated dry run · one real end-to-end send ·
duplicate protection verified in production

**Exit criteria:** real email arrives, correct on desktop and mobile, every story has working source
links, CSVs persist across a redeploy, schedule verified in IST.

---

## Test commands

```bash
.venv\Scripts\python.exe -m pytest -q          # all tests
.venv\Scripts\python.exe -m pytest -q -m unit  # unit only
.venv\Scripts\python.exe -m ruff check .       # lint
```

## Progress

| Phase | Status |
|---|---|
| 0 — Environment & scaffold | ✅ complete |
| 1 — Config, API, auth | ✅ complete |
| 2 — CSV persistence | ✅ complete |
| 3 — Source discovery | ✅ complete |
| 4 — Dedup & clustering | ✅ complete |
| 5 — Ranking | ✅ complete |
| 6 — AI layer | ✅ complete |
| 7 — Email | ✅ complete |
| 8 — Orchestration | next |
| 9 — Deployment | not started |
