"""Phase 4: deduplication and topic clustering (PRD 24, 25, 26).

Clustering quality cannot be judged on a handful of headlines: the IDF model needs a
corpus to measure rarity against, and a three-document corpus makes every word look
equally rare. So the tests here embed their subject headlines in a realistic
background, which is what a live run of ~500 articles actually looks like.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.cluster.clusterer import cluster_articles, clusters_to_topics, make_topic_id
from app.cluster.dedup import deduplicate
from app.cluster.similarity import build_idf, cosine, same_story
from app.cluster.text import token_set, tokenize
from app.cluster.topics import merge_topic_history, reconcile
from app.models import Article, Section

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 9, 1, 7, 30, tzinfo=IST)

BACKGROUND_HEADLINES = [
    "Reserve Bank holds interest rates steady for third quarter",
    "Wildfires force evacuation of coastal towns in Portugal",
    "Census data shows population growth slowing across regional areas",
    "Airline cancels routes after fuel price surge",
    "Hospital trust reports record waiting times this winter",
    "Typhoon makes landfall near southern coast",
    "Protest march draws thousands to the capital",
    "Currency falls to a two-year low against the dollar",
    "Railway workers announce strike over pay dispute",
    "Harvest yields improve after favourable rainfall",
    "Merger between two energy firms clears regulatory review",
    "Vaccine rollout expands to younger age groups",
    "Drought conditions worsen across farming districts",
    "Ferry service suspended following safety inspection",
    "Tariff changes take effect for imported machinery",
    "Summit ends without agreement on emissions targets",
    "Verdict delivered in long-running corruption trial",
    "Budget allocates additional funding to public transport",
    "Election commission announces polling dates",
    "Flood warnings issued for several river catchments",
]


def article(title: str, domain: str = "example.com", published_hours_ago: int = 1) -> Article:
    from app.collect.normalize import article_id, content_hash

    url = f"https://{domain}/{abs(hash(title)) % 10**8}"
    return Article(
        id=article_id(url),
        title=title,
        url=url,
        source=domain,
        source_domain=domain,
        published_at=NOW - dt.timedelta(hours=published_hours_ago),
        collected_at=NOW,
        content_hash=content_hash(title),
    )


def corpus(*headlines: str) -> list[Article]:
    """Subject headlines plus a realistic background, each from a distinct domain."""
    subjects = [article(h, f"outlet{i}.com") for i, h in enumerate(headlines)]
    background = [
        article(h, f"bg{i}.com") for i, h in enumerate(BACKGROUND_HEADLINES)
    ]
    return subjects + background


def cluster_containing(clusters, needle: str):
    for cluster in clusters:
        if any(needle.lower() in a.title.lower() for a in cluster.articles):
            return cluster
    raise AssertionError(f"no cluster contains {needle!r}")


# --- deduplication (PRD 24) -------------------------------------------------


@pytest.mark.unit
def test_identical_urls_collapse():
    duplicate = article("Markets rally on rate decision")
    report = deduplicate([duplicate, duplicate])

    assert len(report.kept) == 1
    assert report.removed_by_url == 1


@pytest.mark.unit
def test_same_story_under_different_urls_collapses_by_content_hash():
    """Syndicated wire copy runs verbatim under many mastheads."""
    wire = "Central bank holds rates steady in split decision"
    report = deduplicate([article(wire, "a.com"), article(wire, "b.com")])

    assert len(report.kept) == 1
    assert report.removed_by_hash == 1


@pytest.mark.unit
def test_cosmetically_different_headlines_collapse_by_title():
    """Level 3 catches what levels 1 and 2 cannot.

    Punctuation-only differences never reach here -- normalisation strips them, so
    those headlines already share a content hash. Level 3 catches reordering, which
    changes the hash but not the sentence.

    Note what level 3 deliberately does *not* catch: "X" versus "X, say officials"
    scores 89.9 and stays two articles. That is the same story told twice, which is
    clustering's job, not deduplication's.
    """
    report = deduplicate(
        [
            article("Central bank holds rates steady in split decision", "a.com"),
            article("In split decision, central bank holds rates steady", "b.com"),
        ]
    )

    assert len(report.kept) == 1
    assert report.removed_by_title == 1
    assert report.removed_by_hash == 0


@pytest.mark.unit
def test_distinct_articles_are_never_deduplicated():
    report = deduplicate(
        [
            article("Central bank holds rates steady", "a.com"),
            article("Wildfires force evacuation of coastal towns", "b.com"),
        ]
    )

    assert len(report.kept) == 2
    assert report.total_removed == 0


@pytest.mark.unit
def test_dedup_keeps_the_more_reliable_copy():
    """The surviving article is what gets cited, so a wire beats an aggregator rewrite."""
    wire = "Central bank holds rates steady in split decision"
    report = deduplicate(
        [article(wire, "contentfarm.com"), article(wire, "reuters.com")],
        reliability={"reuters.com": 0.95, "contentfarm.com": 0.3},
    )

    assert report.kept[0].source_domain == "reuters.com"


# --- clustering: the PRD 25 case --------------------------------------------


@pytest.mark.unit
def test_prd_25_case_collapses_to_one_topic():
    """PRD 25: launches / announces / new model must become ONE topic, not three."""
    articles = corpus(
        "OpenAI launches GPT-5 with major reasoning gains",
        "OpenAI announces GPT-5, its most capable model yet",
        "New OpenAI model GPT-5 tops industry benchmarks",
    )

    clusters = cluster_articles(deduplicate(articles).kept, Section.GLOBAL)

    assert cluster_containing(clusters, "GPT-5").size == 3


@pytest.mark.unit
def test_same_entity_different_event_stays_separate():
    """A lawsuit is not a launch. Sharing one entity is not sharing a story."""
    articles = corpus(
        "OpenAI launches GPT-5 with major reasoning gains",
        "OpenAI announces GPT-5, its most capable model yet",
        "OpenAI faces copyright lawsuit over training data practices",
    )

    clusters = cluster_articles(deduplicate(articles).kept, Section.GLOBAL)

    assert cluster_containing(clusters, "GPT-5").size == 2
    assert cluster_containing(clusters, "lawsuit").size == 1


@pytest.mark.unit
def test_unrelated_stories_never_merge():
    articles = corpus(
        "OpenAI launches GPT-5 with major reasoning gains",
        "Australia beats India in cricket series decider",
    )

    clusters = cluster_articles(deduplicate(articles).kept, Section.GLOBAL)

    assert cluster_containing(clusters, "GPT-5").size == 1
    assert cluster_containing(clusters, "cricket").size == 1


# --- over-merge regressions, found on live data -----------------------------


@pytest.mark.unit
def test_shared_phrase_running_out_does_not_merge_unrelated_stories():
    """Regression. These three merged on the phrase "running out" during calibration.

    They are a cyber-security warning, a water-supply story and a cancer patient
    appeal. The cause was "out", "time" and "running" being absent from the stopword
    list; document frequency could not tell them from real entities, because in a
    454-article corpus "out" and "court" both appeared in 10 documents.
    """
    articles = corpus(
        "Time is running out for cyber security, warn top tech firms",
        "The race to stop England running out of water",
        "Time is running out for teenage cancer patient",
    )

    clusters = cluster_articles(deduplicate(articles).kept, Section.GLOBAL)

    assert cluster_containing(clusters, "cyber security").size == 1
    assert cluster_containing(clusters, "water").size == 1
    assert cluster_containing(clusters, "cancer").size == 1


@pytest.mark.unit
def test_shared_words_man_and_who_do_not_merge_unrelated_stories():
    """Regression: an air-turbulence lawsuit merged with a tennis profile."""
    articles = corpus(
        "Wife of man who died after turbulence sues airline",
        "Who is Mariano Navone? Facts about the man who beat Djokovic",
    )

    clusters = cluster_articles(deduplicate(articles).kept, Section.GLOBAL)

    assert cluster_containing(clusters, "turbulence").size == 1
    assert cluster_containing(clusters, "Navone").size == 1


@pytest.mark.unit
def test_genuine_merges_survive_the_stricter_thresholds():
    """The calibration cases that must still merge after tightening."""
    articles = corpus(
        "Supreme Court allows Trump to move forward with White House ballroom construction",
        "White House construction on $400m ballroom can go on, says US Supreme Court",
        "Lionel Messi says he is retiring from playing internationally for Argentina",
        "Argentinian footballer Lionel Messi announces his retirement",
    )

    clusters = cluster_articles(deduplicate(articles).kept, Section.GLOBAL)

    assert cluster_containing(clusters, "ballroom").size == 2
    assert cluster_containing(clusters, "Messi").size == 2


@pytest.mark.unit
def test_clustering_does_not_chain_transitively():
    """Leader clustering compares against the representative, never any member.

    Single linkage would put A and C together whenever some B matched both, which on
    headlines collapses half a day into one blob.
    """
    articles = corpus(
        "Reserve Bank holds interest rates steady for third quarter",
        "Interest rates steady as inflation eases in monthly figures",
        "Inflation eases across the eurozone according to new figures",
    )

    clusters = cluster_articles(deduplicate(articles).kept, Section.GLOBAL)

    assert cluster_containing(clusters, "eurozone").size <= 2


# --- Telugu ------------------------------------------------------------------


@pytest.mark.unit
def test_telugu_headlines_cluster_together():
    articles = corpus(
        "తెలుగు సినిమా ఇరుముడి బాక్స్ ఆఫీస్ వసూళ్లు",
        "ఇరుముడి బాక్స్ ఆఫీస్ కలెక్షన్లు రవితేజ",
    )

    clusters = cluster_articles(deduplicate(articles).kept, Section.NICHE)

    assert cluster_containing(clusters, "ఇరుముడి").size == 2


@pytest.mark.unit
def test_unrelated_telugu_headlines_stay_separate():
    articles = corpus(
        "తెలుగు సినిమా ఇరుముడి బాక్స్ ఆఫీస్ వసూళ్లు",
        "తెలుగు దర్శకుడు కొత్త పాడ్‌కాస్ట్ ప్రారంభించారు",
    )

    clusters = cluster_articles(deduplicate(articles).kept, Section.NICHE)

    assert cluster_containing(clusters, "ఇరుముడి").size == 1


# --- similarity primitives ---------------------------------------------------


@pytest.mark.unit
def test_stopwords_remove_headline_filler():
    assert "out" not in tokenize("Time is running out for cyber security")
    assert "who" not in tokenize("Who is Mariano Navone")
    assert "cyber" in tokenize("Time is running out for cyber security")


@pytest.mark.unit
def test_idf_gives_rare_tokens_more_weight():
    """A token in one document must outweigh one appearing throughout the corpus."""
    common = [f"Budget debate continues in parliament session {n}" for n in range(10)]
    sets = [token_set(h) for h in [*common, "Quokka spotted in Perth"]]
    idf = build_idf(sets)

    assert idf.weight("quokka") > idf.weight("budget")


@pytest.mark.unit
def test_cosine_is_symmetric_and_bounded():
    sets = [token_set(h) for h in BACKGROUND_HEADLINES]
    idf = build_idf(sets)
    left, right = sets[0], sets[1]

    assert cosine(left, right, idf) == cosine(right, left, idf)
    assert 0.0 <= cosine(left, right, idf) <= 1.0


@pytest.mark.unit
def test_identical_titles_are_always_the_same_story():
    idf = build_idf([token_set(h) for h in BACKGROUND_HEADLINES])

    assert same_story("Markets rally today", "Markets rally today", idf) is True


@pytest.mark.unit
def test_empty_corpus_does_not_crash_clustering():
    assert cluster_articles([], Section.GLOBAL) == []


# --- topic identity and continuity (PRD 13, 26) -----------------------------


@pytest.mark.unit
def test_topic_id_is_deterministic():
    assert make_topic_id("OpenAI launches GPT-5") == make_topic_id("OpenAI launches GPT-5")


@pytest.mark.unit
def test_topic_id_ignores_word_order_and_punctuation():
    assert make_topic_id("Messi retires from Argentina") == make_topic_id(
        "Argentina, Messi retires!"
    )


@pytest.mark.unit
def test_distinct_stories_get_distinct_topic_ids():
    assert make_topic_id("Messi retires") != make_topic_id("Markets rally")


@pytest.mark.unit
def test_topic_id_carries_over_between_runs():
    """PRD 26: momentum needs yesterday and today hanging off one identity."""
    articles = corpus(
        "OpenAI launches GPT-5 with major reasoning gains",
        "OpenAI announces GPT-5, its most capable model yet",
    )

    day_one = reconcile(
        cluster_articles(deduplicate(articles).kept, Section.GLOBAL), [], NOW
    )
    day_two = reconcile(
        cluster_articles(deduplicate(articles).kept, Section.GLOBAL),
        day_one.topics,
        NOW + dt.timedelta(days=1),
    )

    assert day_two.carried_over == len(day_one.topics)
    assert day_two.newly_seen == 0


@pytest.mark.unit
def test_carried_over_topic_keeps_its_original_first_seen():
    """Age is a ranking signal; resetting first_seen would erase it."""
    articles = corpus("OpenAI launches GPT-5 with major reasoning gains")

    day_one = reconcile(
        cluster_articles(deduplicate(articles).kept, Section.GLOBAL), [], NOW
    )
    tomorrow = NOW + dt.timedelta(days=1)
    day_two = reconcile(
        cluster_articles(deduplicate(articles).kept, Section.GLOBAL),
        day_one.topics,
        tomorrow,
    )

    original = {t.topic_id: t.first_seen for t in day_one.topics}
    for topic in day_two.topics:
        assert topic.first_seen == original[topic.topic_id]
        assert topic.last_seen == tomorrow


@pytest.mark.unit
def test_a_genuinely_new_story_is_not_matched_to_yesterday():
    yesterday = reconcile(
        cluster_articles(deduplicate(corpus("Messi retires from Argentina")).kept,
                         Section.GLOBAL),
        [],
        NOW,
    )
    today = reconcile(
        cluster_articles(
            deduplicate(corpus("Volcano erupts near Reykjavik forcing evacuations")).kept,
            Section.GLOBAL,
        ),
        yesterday.topics,
        NOW + dt.timedelta(days=1),
    )

    new_headlines = {t.headline for t in today.topics if t.first_seen > NOW - dt.timedelta(hours=2)}
    assert any("Volcano" in headline for headline in new_headlines)


@pytest.mark.unit
def test_global_and_niche_topics_never_reconcile_across_sections():
    """PRD 23: the two engines stay separate all the way through."""
    shared = "Telugu cinema box office record broken"
    global_run = reconcile(
        cluster_articles(deduplicate(corpus(shared)).kept, Section.GLOBAL), [], NOW
    )
    niche_run = reconcile(
        cluster_articles(deduplicate(corpus(shared)).kept, Section.NICHE),
        global_run.topics,
        NOW,
    )

    assert niche_run.carried_over == 0


@pytest.mark.unit
def test_history_merge_preserves_untouched_topics():
    old = clusters_to_topics(
        cluster_articles(deduplicate(corpus("Messi retires")).kept, Section.GLOBAL), NOW
    )
    current = clusters_to_topics(
        cluster_articles(deduplicate(corpus("Volcano erupts")).kept, Section.GLOBAL), NOW
    )

    merged = merge_topic_history(old, current)

    assert len(merged) >= len(current)
    assert {t.topic_id for t in current} <= {t.topic_id for t in merged}
