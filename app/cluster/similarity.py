"""Story similarity (PRD 24 level 3, PRD 25).

Plain word overlap fails on the exact case the PRD gives. Three headlines about one
launch:

    OpenAI launches GPT-5 with major reasoning gains
    OpenAI announces GPT-5, its most capable model
    New OpenAI model GPT-5 tops benchmarks

share about 20% of their words. What actually ties them together is that they share two
*rare* tokens -- "openai" and "gpt" -- while the words they do not share are common
across the corpus.

So the primary signal is IDF-weighted cosine. But cosine alone is not enough either:
two of six tokens shared caps the score near 0.18, and a threshold that low would merge
unrelated stories. The decision therefore combines two pieces of evidence:

* **cosine** -- overall lexical agreement
* **shared distinctive tokens** -- how many *rare* terms the two headlines have in
  common, which is what "same story" actually looks like in news

Two shared rare entities plus modest cosine is strong evidence. High cosine on its own
is also sufficient. Neither alone at a low value is.
"""

import math
from collections import Counter
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.cluster.text import token_set
from app.collect.normalize import normalize_title

# Above this, two headlines are the same sentence with cosmetic differences.
NEAR_DUPLICATE_TITLE_RATIO = 92.0

# Cosine alone is conclusive at this level.
STRONG_COSINE = 0.45

# Cosine this low is conclusive only when backed by shared distinctive tokens.
# Calibrated against a live 454-article corpus: genuine merges scored 0.29-0.62,
# the worst false merges 0.20-0.23.
SUPPORTED_COSINE = 0.24

# How many shared distinctive tokens count as corroboration.
MIN_SHARED_DISTINCTIVE = 2

# A loose sanity bound, not the main mechanism. Measurement on a real corpus showed
# document frequency cannot separate function words from entities at this scale --
# "out" and "court" both appeared in 10 of 454 articles. Stopword removal does that
# job; this only excludes tokens so ubiquitous they carry no signal at all.
MAX_DISTINCTIVE_DF_RATIO = 0.25


@dataclass
class IdfModel:
    """Inverse document frequency over one run's corpus.

    Deliberately has no maximum-document-frequency cutoff. Zeroing out tokens above a
    frequency ratio looks sensible and is actively harmful here: in a corpus where a
    third of the articles are about one launch, "openai" is the single most useful
    token, not a stopword. Smoothed IDF already down-weights genuinely common terms.
    """

    weights: dict[str, float]
    document_count: int
    document_frequency: dict[str, int]

    def weight(self, token: str) -> float:
        if token in self.weights:
            return self.weights[token]
        # An unseen token is maximally rare, so it earns the highest weight.
        return math.log(self.document_count + 1) + 1.0

    def is_distinctive(self, token: str) -> bool:
        """Rare enough that sharing it is evidence rather than coincidence."""
        if self.document_count == 0:
            return False
        ratio = self.document_frequency.get(token, 0) / self.document_count
        return ratio <= MAX_DISTINCTIVE_DF_RATIO


def build_idf(token_sets: list[frozenset[str]]) -> IdfModel:
    total = len(token_sets)
    if total == 0:
        return IdfModel(weights={}, document_count=0, document_frequency={})

    frequencies: Counter[str] = Counter()
    for tokens in token_sets:
        frequencies.update(tokens)

    weights = {
        token: math.log(total / (1 + count)) + 1.0 for token, count in frequencies.items()
    }

    return IdfModel(
        weights=weights, document_count=total, document_frequency=dict(frequencies)
    )


def cosine(left: frozenset[str], right: frozenset[str], idf: IdfModel) -> float:
    """IDF-weighted cosine over binary term vectors."""
    if not left or not right:
        return 0.0

    shared = left & right
    if not shared:
        return 0.0

    numerator = sum(idf.weight(token) ** 2 for token in shared)
    left_norm = math.sqrt(sum(idf.weight(token) ** 2 for token in left))
    right_norm = math.sqrt(sum(idf.weight(token) ** 2 for token in right))

    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def shared_distinctive(
    left: frozenset[str], right: frozenset[str], idf: IdfModel
) -> frozenset[str]:
    return frozenset(token for token in left & right if idf.is_distinctive(token))


def title_ratio(left: str, right: str) -> float:
    """Fuzzy headline similarity, order-insensitive (rapidfuzz, PRD 24 level 2).

    Compares the normalised forms, not the raw strings. rapidfuzz is case-sensitive
    by default, so raw comparison scores two headlines differing only in
    capitalisation below 100 and lets an exact duplicate through.
    """
    return float(fuzz.token_set_ratio(normalize_title(left), normalize_title(right)))


def are_near_duplicate_titles(left: str, right: str) -> bool:
    return title_ratio(left, right) >= NEAR_DUPLICATE_TITLE_RATIO


def same_story(
    left_title: str,
    right_title: str,
    idf: IdfModel,
    left_tokens: frozenset[str] | None = None,
    right_tokens: frozenset[str] | None = None,
) -> bool:
    """Do these two headlines describe one underlying story?"""
    if are_near_duplicate_titles(left_title, right_title):
        return True

    left = left_tokens if left_tokens is not None else token_set(left_title)
    right = right_tokens if right_tokens is not None else token_set(right_title)

    score = cosine(left, right, idf)
    if score >= STRONG_COSINE:
        return True

    if score < SUPPORTED_COSINE:
        return False

    return len(shared_distinctive(left, right, idf)) >= MIN_SHARED_DISTINCTIVE
