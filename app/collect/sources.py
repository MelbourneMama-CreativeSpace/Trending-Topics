"""Source reliability registry (PRD 16; founder's source-quality tier).

`sources.csv` accumulates evidence about each domain across runs. Two things use it:

* Phase 5 ranking, where reliability weights a source's contribution to breadth
* operators, who can see which feeds have quietly been failing for a week

Because it is accumulated evidence rather than a time series, it is exempt from the
31-day retention sweep -- see the note in `app/storage/datasets.py`.
"""

import datetime as dt

from app.collect.base import SourceOutcome
from app.collect.feeds import reliability_for
from app.models import Source
from app.storage import Dataset, Repository

# A source that has failed this many consecutive times is treated as fully unreliable.
FAILURES_TO_FULL_DISCOUNT = 5


def update_registry(
    repo: Repository, outcomes: list[SourceOutcome], now: dt.datetime
) -> list[Source]:
    """Fold this run's outcomes into the registry and persist it.

    A success clears the consecutive-failure count: a feed that was down yesterday and
    is healthy today should not stay penalised.
    """
    existing: dict[str, Source] = {
        source.source_domain: source
        for source in repo.read(Dataset.SOURCES)
        if isinstance(source, Source)
    }

    for outcome in outcomes:
        if not outcome.source_domain:
            continue

        source = existing.get(outcome.source_domain) or Source(
            source_domain=outcome.source_domain,
            source_name=outcome.source_name,
            source_type=outcome.source_type,
            reliability_score=reliability_for(outcome.source_type),
        )

        if outcome.ok:
            source.last_success = now
            source.failure_count = 0
        else:
            source.last_failure = now
            source.failure_count += 1

        existing[outcome.source_domain] = source

    records = sorted(existing.values(), key=lambda s: s.source_domain)
    repo.write(Dataset.SOURCES, records)
    return records


def effective_reliability(source: Source) -> float:
    """Baseline reliability, discounted by consecutive failures.

    The stored `reliability_score` stays put as the editorial judgement; this is the
    live value ranking should use, so a flapping feed loses weight without anyone
    editing the table.
    """
    if source.failure_count <= 0:
        return source.reliability_score

    penalty = min(source.failure_count / FAILURES_TO_FULL_DISCOUNT, 1.0)
    return round(source.reliability_score * (1.0 - penalty), 4)


def reliability_index(sources: list[Source]) -> dict[str, float]:
    """domain -> effective reliability, for the Phase 5 breadth calculation."""
    return {s.source_domain: effective_reliability(s) for s in sources}
