"""Phase 5: global and niche ranking (PRD 14, 17-23, 26)."""

import ast
import datetime as dt
import inspect
import pathlib
from zoneinfo import ZoneInfo

import pytest

from app.cluster.clusterer import Cluster
from app.models import Article, Section, SourceType
from app.rank import (
    GLOBAL_WEIGHTS,
    NICHE_WEIGHTS,
    RankingContext,
    rank_global,
    rank_niche,
    score_global,
    score_niche,
    select_global_top,
    select_niche_top,
)
from app.rank.history import load_previous_scores
from app.rank.profile import DEFAULT_PROFILE
from app.rank.signals import (
    engagement_score,
    industry_importance_score,
    niche_relevance_score,
    recency_score,
    significance_score,
    source_breadth_score,
    velocity_score,
)
from app.rank.weights import total

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 9, 1, 7, 30, tzinfo=IST)

WIRES = {"reuters.com": 0.95, "bbc.co.uk": 0.95, "apnews.com": 0.95}
FARMS = {f"farm{i}.com": 0.3 for i in range(10)}
RELIABILITY = {**WIRES, **FARMS, "variety.com": 0.85, "randomblog.com": 0.4}
SOURCE_TYPES = {"variety.com": SourceType.INDUSTRY, "reuters.com": SourceType.NEWS}


def article(title, domain, hours_ago=1, category=""):
    return Article(
        id=f"{domain}-{abs(hash(title)) % 10**8}",
        title=title,
        url=f"https://{domain}/{abs(hash(title)) % 10**8}",
        source=domain,
        source_domain=domain,
        published_at=NOW - dt.timedelta(hours=hours_ago),
        collected_at=NOW,
        content_hash=str(abs(hash(title)) % 10**8),
        category=category,
    )


def cluster(headline, domains, section=Section.GLOBAL, hours_ago=1, categories=None):
    categories = categories or [""] * len(domains)
    articles = [
        article(f"{headline} ({i})", domain, hours_ago, categories[i])
        for i, domain in enumerate(domains)
    ]
    articles[0] = article(headline, domains[0], hours_ago, categories[0])
    return Cluster(
        topic_id=f"tid-{abs(hash(headline)) % 10**8}",
        headline=headline,
        section=section,
        articles=articles,
    )


def context(**overrides):
    defaults = {
        "now": NOW,
        "reliability": RELIABILITY,
        "source_types": SOURCE_TYPES,
        "previous_scores": {},
    }
    return RankingContext(**{**defaults, **overrides})


# --- weights and bounds (PRD 19, 22) ----------------------------------------


@pytest.mark.unit
def test_global_weights_match_the_prd():
    assert (
        GLOBAL_WEIGHTS.recency,
        GLOBAL_WEIGHTS.source_breadth,
        GLOBAL_WEIGHTS.velocity,
        GLOBAL_WEIGHTS.engagement,
        GLOBAL_WEIGHTS.significance,
    ) == (0.30, 0.25, 0.20, 0.15, 0.10)


@pytest.mark.unit
def test_niche_weights_match_the_prd():
    assert (
        NICHE_WEIGHTS.recency,
        NICHE_WEIGHTS.source_breadth,
        NICHE_WEIGHTS.velocity,
        NICHE_WEIGHTS.engagement,
        NICHE_WEIGHTS.industry_importance,
        NICHE_WEIGHTS.niche_relevance,
    ) == (0.25, 0.20, 0.20, 0.15, 0.10, 0.10)


@pytest.mark.unit
def test_both_weight_tables_sum_to_one():
    """A table that does not sum to 1 rescales every score and breaks history."""
    assert total(GLOBAL_WEIGHTS) == pytest.approx(1.0)
    assert total(NICHE_WEIGHTS) == pytest.approx(1.0)


@pytest.mark.unit
@pytest.mark.parametrize("section", [Section.GLOBAL, Section.NICHE])
def test_scores_are_bounded_zero_to_one_hundred(section):
    """PRD 19: normalised 0-100. Tested at both extremes of input quality."""
    extremes = [
        cluster(
            "Huge story",
            list(WIRES),
            section,
            hours_ago=0,
            categories=["world", "business", "technology"],
        ),
        cluster("Tiny story", ["randomblog.com"], section, hours_ago=400),
    ]
    scorer = score_global if section is Section.GLOBAL else score_niche

    for item in extremes:
        ranked = scorer(item, context())
        assert 0.0 <= ranked.trend_score <= 100.0
        for name, value in ranked.components.items():
            assert 0.0 <= value <= 100.0, f"{name} out of range"


@pytest.mark.unit
def test_ranking_is_reproducible_on_fixed_input():
    clusters = [cluster(f"Story {i}", list(WIRES)[:2]) for i in range(6)]

    first = [t.topic_id for t in rank_global(clusters, context())]
    second = [t.topic_id for t in rank_global(list(reversed(clusters)), context())]

    assert first == second


# --- contamination: PRD 17 and 23 -------------------------------------------

RANK_DIR = pathlib.Path(inspect.getfile(rank_global)).parent
GLOBAL_PATH_MODULES = ["global_ranker.py", "signals.py", "context.py", "weights.py"]


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.unit
def test_global_ranker_does_not_import_the_founder_profile():
    """PRD 17, enforced structurally rather than by convention.

    Checks the whole import path the global ranker depends on, not just its own file,
    so the profile cannot arrive through a helper module either.
    """
    for filename in GLOBAL_PATH_MODULES:
        imported = _imported_modules(RANK_DIR / filename)
        offending = {name for name in imported if "profile" in name}
        assert not offending, f"{filename} imports {offending}; global ranking must be blind"


@pytest.mark.unit
def test_rank_global_accepts_no_profile_argument():
    """The separation is a property of the signature: you cannot pass a profile."""
    parameters = inspect.signature(rank_global).parameters

    assert "profile" not in parameters
    assert set(parameters) == {"clusters", "context"}


@pytest.mark.unit
def test_ranking_context_carries_no_profile_field():
    assert not any("profile" in name for name in RankingContext.__dataclass_fields__)


@pytest.mark.unit
def test_rank_niche_is_the_only_path_that_takes_a_profile():
    assert "profile" in inspect.signature(rank_niche).parameters


@pytest.mark.unit
def test_global_and_niche_rank_the_same_clusters_differently():
    """PRD 23: separate call paths, so a niche-relevant story ranks differently."""
    clusters = [
        cluster(
            "Telugu cinema short film wins at Melbourne film festival",
            ["variety.com", "randomblog.com"],
        ),
        cluster("Central bank raises interest rates sharply", list(WIRES)),
    ]

    global_first = rank_global(clusters, context())[0].headline
    niche_first = rank_niche(clusters, context())[0].headline

    assert "Central bank" in global_first
    assert "Telugu" in niche_first


# --- recency (PRD 19) -------------------------------------------------------


@pytest.mark.unit
def test_fresher_stories_score_higher_on_recency():
    fresh = recency_score(cluster("A", ["bbc.co.uk"], hours_ago=1), NOW)
    stale = recency_score(cluster("B", ["bbc.co.uk"], hours_ago=36), NOW)

    assert fresh > stale


@pytest.mark.unit
def test_undated_cluster_is_treated_as_unknown_not_fresh():
    """Scoring an undated story 100 would let undated feeds dominate the briefing."""
    undated = Cluster(
        topic_id="t",
        headline="No date",
        section=Section.GLOBAL,
        articles=[
            Article(
                id="a",
                title="No date",
                url="https://e.com/a",
                source="E",
                source_domain="e.com",
                published_at=None,
                collected_at=NOW,
                content_hash="h",
            )
        ],
    )

    assert recency_score(undated, NOW) == 50.0


# --- source breadth: the founder's source-quality requirement ----------------


@pytest.mark.unit
def test_two_wire_services_outrank_ten_content_farms():
    """The core of the source-quality tier.

    Ten low-reliability outlets are usually reprinting one wire story. A linear sum
    of reliability would let them win on headcount, which is backwards.
    """
    wires = source_breadth_score(cluster("A", ["reuters.com", "bbc.co.uk"]), RELIABILITY)
    farms = source_breadth_score(cluster("B", list(FARMS)), RELIABILITY)

    assert wires > farms


@pytest.mark.unit
def test_more_reliable_sources_raise_breadth():
    high = source_breadth_score(cluster("A", ["reuters.com", "bbc.co.uk"]), RELIABILITY)
    low = source_breadth_score(cluster("B", ["farm0.com", "farm1.com"]), RELIABILITY)

    assert high > low


@pytest.mark.unit
def test_unknown_domain_gets_a_neutral_reliability():
    assert source_breadth_score(cluster("A", ["never-seen.com"]), {}) > 0.0


# --- velocity and momentum (PRD 26) -----------------------------------------


@pytest.mark.unit
def test_prd_26_momentum_example():
    """Yesterday 40, today 82: a rise of 42."""
    assert velocity_score(82.0, 40.0) == pytest.approx(71.0)


@pytest.mark.unit
def test_flat_topic_scores_neutral_velocity():
    assert velocity_score(60.0, 60.0) == pytest.approx(50.0)


@pytest.mark.unit
def test_falling_topic_scores_below_neutral():
    assert velocity_score(30.0, 70.0) < 50.0


@pytest.mark.unit
def test_absent_history_is_no_known_movement_not_a_surge():
    """Treating a first sighting as a rise from zero floods the first run after any
    data loss, and makes every new topic look explosive."""
    assert velocity_score(80.0, None) == 50.0


@pytest.mark.unit
def test_rising_topic_outranks_an_equally_popular_flat_one():
    """PRD 26: distinguish already-popular from rapidly-becoming-popular."""
    rising = cluster("Rising story", ["reuters.com", "bbc.co.uk"])
    flat = cluster("Flat story", ["reuters.com", "bbc.co.uk"])

    ranked = rank_global(
        [rising, flat],
        context(previous_scores={rising.topic_id: 10.0, flat.topic_id: 90.0}),
    )

    assert ranked[0].topic_id == rising.topic_id


@pytest.mark.unit
def test_missing_history_never_crashes_ranking():
    """PRD 70: no history is a normal state, not a failure."""
    clusters = [cluster(f"Story {i}", ["bbc.co.uk"]) for i in range(3)]

    ranked = rank_global(clusters, context(previous_scores={}))

    assert len(ranked) == 3
    assert all(t.components["velocity"] == 50.0 for t in ranked)


# --- engagement and significance (proxies) ----------------------------------


@pytest.mark.unit
def test_widely_carried_story_scores_higher_engagement():
    assert engagement_score(cluster("A", list(WIRES))) > engagement_score(
        cluster("B", ["bbc.co.uk"])
    )


@pytest.mark.unit
def test_google_trends_presence_sets_an_engagement_floor():
    """Search volume is the only real engagement evidence available here."""
    trending = cluster("A", ["bbc.co.uk"], categories=["trends"])

    assert engagement_score(trending) >= 70.0


@pytest.mark.unit
def test_wire_coverage_across_desks_scores_more_significant():
    big = cluster("A", list(WIRES), categories=["world", "business", "technology"])
    small = cluster("B", ["randomblog.com"], categories=["world"])

    assert significance_score(big, RELIABILITY) > significance_score(small, RELIABILITY)


# --- niche-only signals (PRD 22, 37, 39) ------------------------------------


@pytest.mark.unit
def test_trade_press_raises_industry_importance():
    trade = cluster("A", ["variety.com"], Section.NICHE)
    general = cluster("B", ["randomblog.com"], Section.NICHE)

    assert industry_importance_score(trade, RELIABILITY, SOURCE_TYPES) > (
        industry_importance_score(general, RELIABILITY, SOURCE_TYPES)
    )


@pytest.mark.unit
def test_relevance_rewards_stories_inside_the_founders_world():
    on_topic = cluster("Telugu short film premieres in Melbourne", ["variety.com"], Section.NICHE)

    assert niche_relevance_score(on_topic, DEFAULT_PROFILE) > 50.0


@pytest.mark.unit
def test_relevance_is_never_forced_upward():
    """PRD 39: an honest zero is correct for a story with no connection."""
    off_topic = cluster("Central bank raises interest rates", list(WIRES), Section.NICHE)

    assert niche_relevance_score(off_topic, DEFAULT_PROFILE) == 0.0


# --- persistence projection (PRD 14) ----------------------------------------


@pytest.mark.unit
def test_global_trend_score_row_carries_zero_relevance():
    """PRD 14: relevance_score is populated for niche ranking only."""
    ranked = score_global(cluster("A", list(WIRES)), context())

    row = ranked.to_trend_score(NOW.date(), Section.GLOBAL)

    assert row.relevance_score == 0.0
    assert row.section == Section.GLOBAL


@pytest.mark.unit
def test_niche_trend_score_row_carries_its_relevance():
    ranked = score_niche(
        cluster("Telugu short film in Melbourne", ["variety.com"], Section.NICHE), context()
    )

    row = ranked.to_trend_score(NOW.date(), Section.NICHE)

    assert row.relevance_score > 0.0
    assert row.section == Section.NICHE


@pytest.mark.unit
def test_trend_score_row_validates_against_the_schema():
    """Every sub-score must satisfy the model's 0-100 bounds."""
    ranked = score_global(cluster("A", list(WIRES)), context())

    row = ranked.to_trend_score(NOW.date(), Section.GLOBAL)

    assert 0.0 <= row.trend_score <= 100.0


# --- selection --------------------------------------------------------------


@pytest.mark.unit
def test_selection_returns_at_most_top_n():
    """Niche clusters must be niche-relevant to survive the eligibility gate."""
    generic = [cluster(f"Story {i}", list(WIRES)[:2]) for i in range(9)]
    relevant = [
        cluster(f"Telugu short film story {i}", list(WIRES)[:2], Section.NICHE) for i in range(9)
    ]

    assert len(select_global_top(generic, context(), top_n=5)) == 5
    assert len(select_niche_top(relevant, context(), top_n=5)) == 5


@pytest.mark.unit
def test_selection_returns_everything_when_fewer_than_top_n():
    """PRD 31: three verified topics beat five fabricated ones."""
    clusters = [cluster(f"Story {i}", ["bbc.co.uk"]) for i in range(3)]

    assert len(select_global_top(clusters, context(), top_n=5)) == 3


@pytest.mark.unit
def test_ranking_an_empty_list_is_safe():
    assert rank_global([], context()) == []
    assert rank_niche([], context()) == []


@pytest.mark.unit
def test_results_are_ordered_by_descending_score():
    clusters = [
        cluster("Strong", list(WIRES), hours_ago=0, categories=["world", "business", "technology"]),
        cluster("Weak", ["randomblog.com"], hours_ago=200),
    ]

    ranked = rank_global(clusters, context())

    assert ranked[0].trend_score > ranked[1].trend_score
    assert ranked[0].headline == "Strong"


# --- history loading (PRD 26) -----------------------------------------------


@pytest.mark.unit
def test_history_loads_the_most_recent_prior_day(repo):
    from app.models import TrendScore
    from app.storage import Dataset

    repo.write(
        Dataset.TREND_SCORES,
        [
            TrendScore(
                topic_id="t1", date=dt.date(2026, 8, 30), section=Section.GLOBAL, trend_score=40.0
            ),
            TrendScore(
                topic_id="t1", date=dt.date(2026, 8, 31), section=Section.GLOBAL, trend_score=62.0
            ),
        ],
    )

    scores = load_previous_scores(repo, Section.GLOBAL, dt.date(2026, 9, 1))

    assert scores == {"t1": 62.0}


@pytest.mark.unit
def test_history_ignores_the_other_section(repo):
    from app.models import TrendScore
    from app.storage import Dataset

    repo.write(
        Dataset.TREND_SCORES,
        [
            TrendScore(
                topic_id="n1", date=dt.date(2026, 8, 31), section=Section.NICHE, trend_score=70.0
            ),
        ],
    )

    assert load_previous_scores(repo, Section.GLOBAL, dt.date(2026, 9, 1)) == {}


@pytest.mark.unit
def test_history_survives_a_missed_run(repo):
    """Using the latest available day, not literally yesterday, so one skipped
    morning does not erase every topic's momentum."""
    from app.models import TrendScore
    from app.storage import Dataset

    repo.write(
        Dataset.TREND_SCORES,
        [
            TrendScore(
                topic_id="t1", date=dt.date(2026, 8, 25), section=Section.GLOBAL, trend_score=55.0
            ),
        ],
    )

    assert load_previous_scores(repo, Section.GLOBAL, dt.date(2026, 9, 1)) == {"t1": 55.0}


@pytest.mark.unit
def test_history_is_empty_on_a_first_run(repo):
    assert load_previous_scores(repo, Section.GLOBAL, dt.date(2026, 9, 1)) == {}


# --- Telugu relevance, and the Creative Radar eligibility gate ---------------


@pytest.mark.unit
def test_telugu_script_headlines_score_relevance():
    """Regression: the profile held only English terms, so substring-matching
    "telugu" never fired on "తెలుగు".

    Every Telugu-language article scored zero relevance -- 200 of 731 niche articles
    in a live run, including a headline that literally reads "Telugu short film".
    The highest-value content in the product was being excluded from its own section.
    """
    telugu = cluster("ప్రయాణం || తెలుగు షార్ట్ ఫిల్మ్", ["telugu360.com"], Section.NICHE)

    assert niche_relevance_score(telugu, DEFAULT_PROFILE) > 0.0


@pytest.mark.unit
def test_telugu_and_english_headlines_both_reach_creative_radar():
    clusters = [
        cluster("తెలుగు సినిమా టాలీవుడ్ కొత్త దర్శకుడు", ["gulte.com"], Section.NICHE),
        cluster("Telugu short film selected for a festival", ["variety.com"], Section.NICHE),
    ]

    selected = select_niche_top(clusters, context(), top_n=5)

    assert len(selected) == 2


@pytest.mark.unit
def test_zero_relevance_topic_is_excluded_from_creative_radar():
    """A live run put "Love Island USA Returns for Season 8 Reunion" at the top of
    Creative Radar: zero founder relevance, winning on trade coverage and recency.

    At 10% of the weight, relevance cannot by itself keep an unrelated story out, so
    selection gates on it separately.
    """
    clusters = [
        cluster(
            "Love Island USA returns for season 8 reunion",
            ["variety.com"],
            Section.NICHE,
            hours_ago=0,
        ),
        cluster(
            "Telugu short film premieres in Melbourne",
            ["randomblog.com"],
            Section.NICHE,
            hours_ago=20,
        ),
    ]

    selected = select_niche_top(clusters, context(), top_n=5)

    assert len(selected) == 1
    assert "Telugu" in selected[0].headline


@pytest.mark.unit
def test_the_gate_does_not_change_the_prd_weights():
    """PRD 22 weights are applied unchanged; only selection is gated."""
    assert NICHE_WEIGHTS.niche_relevance == 0.10


@pytest.mark.unit
def test_gated_out_topics_are_still_ranked_and_recorded():
    """trend_scores.csv keeps every topic; the gate only affects what is sent."""
    clusters = [
        cluster("Love Island USA returns for season 8", ["variety.com"], Section.NICHE),
        cluster("Telugu short film premieres in Melbourne", ["variety.com"], Section.NICHE),
    ]

    assert len(rank_niche(clusters, context())) == 2
    assert len(select_niche_top(clusters, context())) == 1


@pytest.mark.unit
def test_gate_returns_fewer_than_top_n_rather_than_padding():
    """PRD 31: three verified topics beat five padded ones."""
    clusters = [
        cluster(f"Unrelated business story {i}", ["randomblog.com"], Section.NICHE)
        for i in range(8)
    ]

    assert select_niche_top(clusters, context(), top_n=5) == []


@pytest.mark.unit
def test_global_selection_is_not_gated_on_relevance():
    """PRD 17: the global engine has no notion of founder relevance at all."""
    clusters = [cluster(f"Central bank story {i}", list(WIRES)) for i in range(5)]

    assert len(select_global_top(clusters, context(), top_n=5)) == 5
