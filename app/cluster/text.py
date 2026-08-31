"""Tokenisation for similarity (PRD 24, 25).

Works on the output of `normalize_title`, which has already stripped punctuation by
Unicode category and preserved Telugu combining marks. Splitting on whitespace is
therefore enough, and works identically for both scripts.
"""

from app.collect.normalize import normalize_title

# Function words and headline filler. This list has to be genuinely comprehensive:
# document frequency cannot do this job for us. In a 454-article corpus, "out", "man"
# and "who" each appear in ~10 documents -- exactly the same as "court" and "apple".
# Measured, not assumed. So rarity cannot distinguish a function word from an entity at
# this corpus size, and anything left in the vocabulary becomes evidence of similarity.
# Leaving these in merged "Time is running out for cyber security" with "The race to
# stop England running out of water".
ENGLISH_STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for from by
with without about into over after before during under above below between through
as is are was were be been being am it its their our your my his her hers ours yours
we you they he she i us them me him himself herself itself themselves
not no nor so such can could will would shall should may might must do does did done
has have had having get gets got give gives gave take takes took make makes made
say says said tell tells told see sees saw look looks looked go goes went come comes
new news latest update updates report reports reported live breaking exclusive
what when where which who whom whose why how whether
out up down off away back on-again more most much many few less least very too also
just still even only own same other another each every both all any some none
one two three first second last next previous top best worst big small great good bad
man woman men women people person time times year years day days week weeks month
months today yesterday tomorrow morning night way ways thing things part parts
here there now then again once ever never always often sometimes
amid amidst despite against among along across around behind beyond within
plan plans set sets sees calls call name names put puts help helps use uses using
""".split())

# High-frequency Telugu function words and headline filler.
TELUGU_STOPWORDS = frozenset("""
మరియు లేదా కానీ ఈ ఆ ఇది అది ఇవి అవి వార్తలు తాజా కొత్త అని అంటే ఉంది ఉన్న ఉన్నారు
చేసిన చేస్తున్న కోసం నుండి తో పై లో కి కు ను గా అయిన వల్ల అయితే మరి ఇప్పుడు అలాగే
ఎందుకు ఏమిటి ఎవరు ఎక్కడ ఎప్పుడు చాలా కూడా అన్ని ఒక ఒకటి రెండు మొదటి చివరి
""".split())

STOPWORDS = ENGLISH_STOPWORDS | TELUGU_STOPWORDS

MIN_TOKEN_LENGTH = 2


def tokenize(title: str) -> list[str]:
    """Content tokens from a headline, in order, with duplicates preserved."""
    return [
        token
        for token in normalize_title(title).split()
        if len(token) >= MIN_TOKEN_LENGTH and token not in STOPWORDS
    ]


def token_set(title: str) -> frozenset[str]:
    return frozenset(tokenize(title))


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Plain set overlap. Used only as a guard rail, never as the primary signal."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
