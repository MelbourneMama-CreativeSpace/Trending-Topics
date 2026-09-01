"""Structured AI outputs and their validation rules (PRD 33, 34, 66, 67).

`extra="forbid"` throughout: PRD 67 requires rejecting unsupported fields where a
strict schema applies. A model that invents a field has misunderstood the contract,
and silently dropping it hides that.

Note what the model is *not* asked for: URLs. It cites sources by index into a list we
supplied, and the final source objects are rebuilt from our own retrieved data. A
model cannot fabricate a reference it was never asked to produce (PRD 35).
"""

from pydantic import BaseModel, ConfigDict, Field

MAX_HEADLINE_LENGTH = 200
MAX_KEY_FACTS = 4
MAX_UNCERTAINTIES = 3


class BriefSource(BaseModel):
    """A citation in the email (PRD 34). Built from retrieved data, never from the model."""

    title: str
    url: str
    publisher: str


class TopicBrief(BaseModel):
    """One topic's research and summary (PRD 33, 66)."""

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=MAX_HEADLINE_LENGTH)
    what_happened: str = Field(min_length=1)
    why_trending: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    key_facts: list[str] = Field(default_factory=list, max_length=MAX_KEY_FACTS)
    uncertainties: list[str] = Field(default_factory=list, max_length=MAX_UNCERTAINTIES)
    creative_angle: str | None = None
    conflict_detected: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    source_indices: list[int] = Field(default_factory=list)
    """1-based indices into the source list supplied in the prompt."""


class SparkIdea(BaseModel):
    """The Creative Spark (PRD 41). Optional by design."""

    model_config = ConfigDict(extra="forbid")

    idea: str = Field(min_length=1)
    format: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
