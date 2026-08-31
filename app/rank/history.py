"""Previous-day scores for momentum (PRD 26).

Absent history is a normal state -- the first run ever, a restored backup, a corrupt
trend_scores.csv. It must degrade to "no known movement", never to an exception.
"""

import datetime as dt

from app.models import Section, TrendScore
from app.storage import Dataset, Repository


def load_previous_scores(
    repo: Repository, section: Section, before: dt.date
) -> dict[str, float]:
    """Scores from the most recent day strictly before `before`, for this section.

    Uses the latest available day rather than exactly yesterday, so a missed run does
    not silently erase every topic's momentum.
    """
    rows = [
        row
        for row in repo.read(Dataset.TREND_SCORES)
        if isinstance(row, TrendScore) and row.section == section and row.date < before
    ]
    if not rows:
        return {}

    latest_day = max(row.date for row in rows)
    return {row.topic_id: row.trend_score for row in rows if row.date == latest_day}
