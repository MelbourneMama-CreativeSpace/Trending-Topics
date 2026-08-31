"""The founder profile (PRD 38).

This module is imported by the niche ranker and by nothing else. PRD 17 requires the
global engine to be blind to these interests, and `tests/test_ranking.py` asserts that
`app/rank/global_ranker.py` neither imports this module nor accepts a profile argument
-- so the separation is enforced, not merely intended.
"""

from dataclasses import dataclass, field

# Terms that mark a story as belonging to this founder's world, with how strongly.
# Weights are editorial judgement, not measurements.
DEFAULT_INTERESTS: dict[str, float] = {
    # Core identity
    "telugu": 1.0,
    "tollywood": 1.0,
    "telugu cinema": 1.0,
    # Craft
    "filmmaking": 0.9,
    "screenwriting": 0.8,
    "cinematography": 0.8,
    "short film": 0.9,
    "documentary": 0.7,
    "vfx": 0.7,
    "editing": 0.6,
    "virtual production": 0.8,
    # Formats and distribution
    "podcast": 0.9,
    "youtube": 0.8,
    "ott": 0.8,
    "web series": 0.7,
    "streaming": 0.6,
    "creator economy": 0.9,
    "short form": 0.7,
    # Technology
    "ai filmmaking": 1.0,
    "generative ai": 0.7,
    "film technology": 0.8,
    # Place and community
    "melbourne": 0.9,
    "australia": 0.6,
    "indian cinema": 0.7,
    "film festival": 0.7,
}

# Telugu-script equivalents. Without these, every Telugu-language article scores zero
# relevance -- substring matching "telugu" never fires on "తెలుగు". That silently
# excluded the highest-value content in the product from its own Creative Radar:
# 200 of 731 niche articles in a live run, including headlines that literally read
# "Telugu short film".
TELUGU_INTERESTS: dict[str, float] = {
    "తెలుగు": 1.0,          # telugu
    "టాలీవుడ్": 1.0,         # tollywood
    "సినిమా": 0.9,          # cinema
    "సినీ": 0.8,            # cine-
    "షార్ట్ ఫిల్మ్": 1.0,      # short film
    "ఫిల్మ్": 0.8,           # film
    "పాడ్‌కాస్ట్": 0.9,        # podcast
    "దర్శకుడు": 0.9,        # director
    "దర్శకులు": 0.9,        # directors
    "నటుడు": 0.7,           # actor
    "నటులు": 0.7,           # actors
    "ఓటీటీ": 0.8,           # OTT
    "వెబ్ సిరీస్": 0.7,       # web series
    "యూట్యూబ్": 0.8,        # youtube
    "మెల్‌బోర్న్": 0.9,       # melbourne
    "ఆస్ట్రేలియా": 0.6,       # australia
}


@dataclass(frozen=True)
class FounderProfile:
    """PRD 38: Melbourne Mama Creative Space."""

    name: str = "Melbourne Mama Creative Space"
    interests: dict[str, float] = field(
        default_factory=lambda: {**DEFAULT_INTERESTS, **TELUGU_INTERESTS}
    )

    def matched_terms(self, text: str) -> dict[str, float]:
        """Interest terms present in the text, with their weights."""
        lowered = text.casefold()
        return {term: weight for term, weight in self.interests.items() if term in lowered}


DEFAULT_PROFILE = FounderProfile()
