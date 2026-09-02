"""The brand portfolio (supersedes the single founder profile for the niche section).

Creative Radar used to be one list scored against one set of interests. That worked
while the niche was "Telugu cinema and filmmaking", but the studio is ten pages with
ten different lanes -- a smartphone launch matters to The Tech Gun and is noise to Eat
Post Share, and a restaurant opening is the reverse.

So each brand carries its own interest terms and its own search queries, and the
briefing gives each one its own block. `interests` drives ranking; `queries` drives
what gets collected in the first place, because a story nobody searched for can never
be ranked.

Terms are lowercase and matched as substrings against headline text, so `"reel"` also
catches `"reels"`. Telugu terms sit alongside English ones for the same reason they do
in the founder profile: substring-matching `"telugu"` never fires on `తెలుగు`, and
Telugu-language coverage is a large share of what these pages actually care about.

Lives at the top level rather than under `app/rank` because collection needs the
queries and ranking needs the interests. Putting it under `rank` would have forced
`collect` to import from `rank`, inverting the pipeline's layering.

Nothing on the global path may import this. PRD 17 requires the global engine to stay
blind to the founder's interests, and `tests/test_ranking.py` enforces that by parsing
the global ranker's import graph -- brands are exactly the contamination it guards
against.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Brand:
    """One page in the portfolio."""

    key: str
    name: str
    tagline: str
    icon: str
    interests: dict[str, float]
    queries: tuple[str, ...]
    telugu_queries: tuple[str, ...] = ()

    def matched_terms(self, text: str) -> dict[str, float]:
        lowered = text.casefold()
        return {term: weight for term, weight in self.interests.items() if term in lowered}


BRANDS: tuple[Brand, ...] = (
    Brand(
        key="melbourne_mama",
        name="Melbourne MAMA",
        tagline="International Students ADDA · Community · Media · Being NRI",
        icon="🎓",
        interests={
            "international student": 1.0,
            "indian student": 1.0,
            "student visa": 1.0,
            "graduate visa": 0.9,
            "permanent residency": 0.85,
            "immigration": 0.8,
            "migration": 0.8,
            "diaspora": 0.9,
            "nri": 0.9,
            "indian community": 0.9,
            "telugu community": 1.0,
            "melbourne": 0.85,
            "australia": 0.6,
            "visa": 0.7,
            "overseas student": 0.9,
            "వీసా": 0.9,
            "ఆస్ట్రేలియా": 0.85,
        },
        queries=(
            "Indian students Australia visa",
            "Australia international student policy",
            "Indian diaspora Australia community",
            "Telugu community Australia",
        ),
        telugu_queries=("ఆస్ట్రేలియా తెలుగు వార్తలు",),
    ),
    Brand(
        key="creativespace",
        name="Creativespace.mm",
        tagline="Indo-Australian content studio · Podcasts · Video · Storytelling",
        icon="🎙",
        interests={
            "podcast": 1.0,
            "storytelling": 0.9,
            "content studio": 0.9,
            "video production": 0.9,
            "documentary": 0.8,
            "creator economy": 0.85,
            "branded content": 0.8,
            "film production": 0.75,
            "audio": 0.6,
            "పాడ్‌కాస్ట్": 1.0,
        },
        queries=(
            "podcast industry news",
            "video production trends",
            "branded content studio",
            "Indian podcast growth",
        ),
    ),
    Brand(
        key="contentuu",
        name="MAMA Contentuu",
        tagline="Reels · Sketches · Micro Drama",
        icon="🎭",
        interests={
            "reel": 1.0,
            "short form": 1.0,
            "youtube shorts": 1.0,
            "instagram": 0.85,
            "tiktok": 0.85,
            "micro drama": 1.0,
            "microdrama": 1.0,
            "vertical video": 0.95,
            "sketch comedy": 0.95,
            "web series": 0.8,
            "short video": 0.9,
            "షార్ట్": 0.9,
        },
        queries=(
            "Instagram Reels update creators",
            "YouTube Shorts monetisation",
            "micro drama vertical series",
            "short form video trends",
        ),
    ),
    Brand(
        key="satish_varma",
        name="Satish Varma",
        tagline="Business Consultant · Founder & CEO · TEDx Speaker",
        icon="💼",
        interests={
            "entrepreneur": 1.0,
            "startup": 0.9,
            "founder": 0.9,
            "small business": 0.9,
            "business consulting": 0.9,
            "community building": 0.9,
            "tedx": 1.0,
            "public speaking": 0.85,
            "leadership": 0.7,
            "funding": 0.7,
            "diaspora business": 1.0,
        },
        queries=(
            "Indian entrepreneurs Australia business",
            "small business Australia news",
            "startup funding India",
            "diaspora business leadership",
        ),
    ),
    Brand(
        key="eat_post_share",
        name="Eat Post Share",
        tagline="Food & Beverages · Reviews · PR",
        icon="🍽",
        interests={
            "restaurant": 1.0,
            "food": 0.9,
            "beverage": 0.9,
            "cafe": 0.9,
            "dining": 0.9,
            "chef": 0.85,
            "food festival": 0.95,
            "menu": 0.7,
            "hospitality": 0.8,
            "food review": 1.0,
            "indian restaurant": 1.0,
        },
        queries=(
            "Melbourne restaurant opening",
            "food and beverage trends",
            "Indian restaurant Australia",
            "food festival Australia",
        ),
    ),
    Brand(
        key="the_cheguri",
        name="The Cheguri",
        tagline="Daily vlogs in Telugu",
        icon="📹",
        interests={
            "vlog": 1.0,
            "vlogger": 1.0,
            "daily vlog": 1.0,
            "youtube channel": 0.8,
            "lifestyle": 0.7,
            "day in the life": 0.9,
            "telugu youtube": 1.0,
            "వ్లాగ్": 1.0,
            "యూట్యూబ్": 0.85,
        },
        queries=(
            "Telugu vlogger YouTube",
            "daily vlog creator news",
            "YouTube vlogging trends",
        ),
        telugu_queries=("తెలుగు వ్లాగ్",),
    ),
    Brand(
        key="the_tech_gun",
        name="The Tech Gun",
        tagline="Daily Tech Videos · Unboxing, Reviews & Tips",
        icon="📱",
        interests={
            "smartphone": 1.0,
            "gadget": 1.0,
            "unboxing": 1.0,
            "tech review": 1.0,
            "laptop": 0.9,
            "android": 0.9,
            "iphone": 0.9,
            "wearable": 0.85,
            "launch": 0.6,
            "processor": 0.7,
            "earbuds": 0.85,
            "టెక్": 0.9,
        },
        queries=(
            "smartphone launch India",
            "gadget review unboxing",
            "Android feature update",
            "consumer tech India launch",
        ),
    ),
    Brand(
        key="cheptha_vintava",
        name="Cheptha Vintava",
        tagline="Local to global news, fast & quirky, in Telugu",
        icon="⚡",
        interests={
            "viral": 1.0,
            "trending": 0.9,
            "explainer": 0.9,
            "unusual": 0.9,
            "bizarre": 0.9,
            "record": 0.7,
            "study finds": 0.8,
            "world first": 0.9,
            "వైరల్": 1.0,
            "వార్తలు": 0.8,
        },
        queries=(
            "viral news India",
            "unusual world news story",
            "trending explainer news",
        ),
        telugu_queries=("వైరల్ వార్తలు",),
    ),
    Brand(
        key="mama_matters",
        name="MAMA Matters",
        tagline="For creators. By creators. Content, Culture & Creator Life",
        icon="✨",
        interests={
            "creator economy": 1.0,
            "monetisation": 1.0,
            "monetization": 1.0,
            "influencer": 0.95,
            "brand deal": 1.0,
            "algorithm": 0.9,
            "creator burnout": 1.0,
            "platform policy": 0.95,
            "youtube": 0.8,
            "subscriber": 0.8,
            "ad revenue": 0.9,
            "క్రియేటర్": 1.0,
        },
        queries=(
            "creator monetisation platform news",
            "YouTube algorithm creators",
            "influencer marketing India",
            "creator economy report",
        ),
    ),
    Brand(
        key="sariggaa_choodu",
        name="Sariggaa Choodu",
        tagline="Movies, Music & Cricket — from a blind creator's view",
        icon="🎧",
        interests={
            "cricket": 1.0,
            "movie": 0.95,
            "film": 0.9,
            "music": 0.95,
            "album": 0.85,
            "accessibility": 1.0,
            "blind": 1.0,
            "disability": 1.0,
            "audio description": 1.0,
            "screen reader": 1.0,
            "telugu cinema": 0.95,
            "సినిమా": 0.9,
            "క్రికెట్": 1.0,
        },
        queries=(
            "cricket news India match",
            "Telugu movie music release",
            "accessibility disability technology",
            "audio description film accessibility",
        ),
    ),
)

BRANDS_BY_KEY: dict[str, Brand] = {brand.key: brand for brand in BRANDS}


def all_queries() -> tuple[tuple[str, str], ...]:
    """Every brand query as (query, language), for building collection feeds."""
    pairs: list[tuple[str, str]] = []
    for brand in BRANDS:
        pairs.extend((query, "en") for query in brand.queries)
        pairs.extend((query, "te") for query in brand.telugu_queries)
    return tuple(pairs)
