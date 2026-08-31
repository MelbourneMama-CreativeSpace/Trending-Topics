"""The five CSV datasets (PRD 7) and their retention rules."""

from dataclasses import dataclass
from enum import StrEnum
from typing import get_args

from pydantic import BaseModel

from app.models import Article, Briefing, Source, Topic, TrendScore


class Dataset(StrEnum):
    ARTICLES = "articles"
    TOPICS = "topics"
    TREND_SCORES = "trend_scores"
    BRIEFINGS = "briefings"
    SOURCES = "sources"


@dataclass(frozen=True)
class DatasetSpec:
    model: type[BaseModel]
    retention_field: str | None
    """Column the 31-day cutoff is applied to. `None` means never pruned by date."""

    @property
    def fieldnames(self) -> list[str]:
        """CSV header, in the PRD's declared column order."""
        return list(self.model.model_fields)

    @property
    def nullable_fields(self) -> frozenset[str]:
        """Fields where an empty CSV cell means None rather than an empty string.

        Without this distinction, `category` (a `str` defaulting to "") and
        `topic_id` (a `str | None`) would round-trip identically and one of them
        would fail validation on read.
        """
        return frozenset(
            name
            for name, field in self.model.model_fields.items()
            if type(None) in get_args(field.annotation)
        )


DATASET_SPECS: dict[Dataset, DatasetSpec] = {
    Dataset.ARTICLES: DatasetSpec(Article, "collected_at"),
    Dataset.TOPICS: DatasetSpec(Topic, "last_seen"),
    Dataset.TREND_SCORES: DatasetSpec(TrendScore, "date"),
    Dataset.BRIEFINGS: DatasetSpec(Briefing, "briefing_date"),
    # sources.csv is a registry keyed by domain, not a time series. It is deliberately
    # exempt from the 31-day cutoff: reliability_score is accumulated evidence, and
    # Phase 5 ranking depends on it. Pruning it would reset every source's reputation
    # each month and silently flatten the source-quality weighting. Its size is bounded
    # by the number of distinct domains, not by time.
    Dataset.SOURCES: DatasetSpec(Source, None),
}


def spec_for(dataset: Dataset) -> DatasetSpec:
    return DATASET_SPECS[dataset]


def filename_for(dataset: Dataset) -> str:
    return f"{dataset.value}.csv"
