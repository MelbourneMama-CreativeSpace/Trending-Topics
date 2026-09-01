"""Email rendering (PRD 42-47).

Produces the HTML body, a plain-text fallback and the subject line.

Autoescaping is on and must stay on. Everything rendered here originates either from a
third-party feed or from a language model, so escaping is the boundary that stops a
headline containing `<script>` from becoming markup in someone's inbox.
"""

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from jinja2 import Environment, select_autoescape

from app.ai.briefing import ResearchedTopic
from app.ai.schemas import SparkIdea
from app.collect.urls import is_valid_url
from app.mailer.template import (
    ACCENT,
    CARD,
    CARD_MACRO,
    CATEGORY_ICONS,
    HTML_TEMPLATE,
    INK,
    MUTED,
    PAPER,
    RULE,
)

DEFAULT_ICON = "📰"

GLOBAL_INTRO = "The biggest stories worth knowing today, chosen without reference to your work."
NICHE_INTRO = (
    "Trends from Telugu cinema, filmmaking, podcasting and the Melbourne creative scene."
)

_environment = Environment(
    autoescape=select_autoescape(default=True, default_for_string=True),
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass
class Briefing:
    """Everything the renderer needs. Assembled by the Phase 8 pipeline."""

    briefing_date: dt.date
    timezone: str
    global_topics: list[ResearchedTopic] = field(default_factory=list)
    niche_topics: list[ResearchedTopic] = field(default_factory=list)
    spark: SparkIdea | None = None
    expected_global: int = 5
    expected_niche: int = 5
    generated_at: dt.datetime | None = None

    @property
    def is_partial(self) -> bool:
        return (
            len(self.global_topics) < self.expected_global
            or len(self.niche_topics) < self.expected_niche
        )

    @property
    def source_count(self) -> int:
        return len(
            {
                source.url
                for topic in (*self.global_topics, *self.niche_topics)
                for source in topic.sources
            }
        )


def category_icon(category: str) -> str:
    return CATEGORY_ICONS.get(category.lower().strip(), DEFAULT_ICON)


def build_subject(briefing_date: dt.date) -> str:
    """PRD 47, with the date so a missed morning is obvious in the inbox."""
    # Built from parts rather than one strftime: %-d is not portable to Windows.
    return (
        f"🌅 Morning Intelligence — {briefing_date:%b} "
        f"{briefing_date.day}, {briefing_date.year}"
    )


def shortfall_note(count: int, expected: int, label: str) -> str:
    """PRD 62: say plainly that the day was thin. Never invent a filler story."""
    if count >= expected:
        return ""
    if count == 0:
        return f"No {label} trends could be verified today."
    return (
        f"{count} verified {label} {'trend was' if count == 1 else 'trends were'} "
        f"available today, rather than the usual {expected}. Nothing has been "
        f"invented to fill the gap."
    )


def _date_line(briefing: Briefing) -> str:
    zone = ZoneInfo(briefing.timezone)
    label = briefing.briefing_date.strftime("%A, %B ")
    return (
        f"{label}{briefing.briefing_date.day}, {briefing.briefing_date.year}"
        f" · 7:30 AM {_zone_abbreviation(zone, briefing.briefing_date)}"
    )


def _zone_abbreviation(zone: ZoneInfo, day: dt.date) -> str:
    return dt.datetime(day.year, day.month, day.day, 7, 30, tzinfo=zone).strftime("%Z")


def _preheader(briefing: Briefing) -> str:
    """Inbox preview text. Clients show it beside the subject."""
    lead = briefing.global_topics[0].headline if briefing.global_topics else ""
    return f"{len(briefing.global_topics)} global + {len(briefing.niche_topics)} creative. {lead}"


def _valid_sources_only(topics: list[ResearchedTopic]) -> list[ResearchedTopic]:
    """Last gate before rendering: only validated URLs may become links (PRD 68)."""
    cleaned = []
    for topic in topics:
        safe = [source for source in topic.sources if is_valid_url(source.url)]
        cleaned.append(topic if len(safe) == len(topic.sources) else _replace_sources(topic, safe))
    return cleaned


def _replace_sources(topic: ResearchedTopic, sources) -> ResearchedTopic:
    from dataclasses import replace

    return replace(topic, sources=sources)


def render_html(briefing: Briefing) -> str:
    template = _environment.from_string(CARD_MACRO + HTML_TEMPLATE)
    generated = briefing.generated_at or dt.datetime.now(ZoneInfo(briefing.timezone))

    return template.render(
        subject=build_subject(briefing.briefing_date),
        preheader=_preheader(briefing),
        date_line=_date_line(briefing),
        generated_line=generated.strftime("%H:%M %Z"),
        global_topics=_valid_sources_only(briefing.global_topics),
        niche_topics=_valid_sources_only(briefing.niche_topics),
        spark=briefing.spark,
        global_intro=GLOBAL_INTRO,
        niche_intro=NICHE_INTRO,
        niche_shortfall=shortfall_note(
            len(briefing.niche_topics), briefing.expected_niche, "creative"
        ),
        source_count=briefing.source_count,
        icon=category_icon,
        ink=INK,
        muted=MUTED,
        accent=ACCENT,
        paper=PAPER,
        card_bg=CARD,
        rule=RULE,
    )


def render_text(briefing: Briefing) -> str:
    """Plain-text fallback (PRD 46).

    Not a stripped copy of the HTML: it is laid out to be read on its own, because
    some clients and most screen readers will only ever see this version.
    """
    rule = "=" * 66
    lines: list[str] = [
        "MELBOURNE MAMA",
        "MORNING INTELLIGENCE",
        _date_line(briefing),
        rule,
        "",
    ]

    def section(title: str, intro: str, topics: list[ResearchedTopic], niche: bool) -> None:
        lines.extend([title, intro, ""])
        for position, topic in enumerate(topics, 1):
            label = f" [{topic.category}]" if topic.category else ""
            lines.append(f"{position:02d}.{label} {topic.headline}")
            lines.append("")
            lines.append(f"    WHAT HAPPENED   {topic.what_happened}")
            lines.append(f"    WHY TRENDING    {topic.why_trending}")
            lines.append(f"    WHY IT MATTERS  {topic.why_it_matters}")
            if niche and topic.creative_angle:
                lines.append(f"    CREATIVE ANGLE  {topic.creative_angle}")
                lines.append("                    (an AI suggestion, not reporting)")
            if topic.uncertainties:
                heading = "SOURCES DISAGREE" if topic.conflict_detected else "STILL UNCLEAR"
                lines.append(f"    {heading:15} {'; '.join(topic.uncertainties[:2])}")
            lines.append("    SOURCES")
            for source in topic.sources:
                if is_valid_url(source.url):
                    lines.append(f"      - {source.publisher}: {source.url}")
            lines.extend(["", "-" * 66, ""])

    section("GLOBAL PULSE", GLOBAL_INTRO, briefing.global_topics, niche=False)

    shortfall = shortfall_note(len(briefing.niche_topics), briefing.expected_niche, "creative")
    section("CREATIVE RADAR", NICHE_INTRO, briefing.niche_topics, niche=True)
    if shortfall:
        lines.extend([f"NOTE: {shortfall}", ""])

    if briefing.spark:
        lines.extend([
            rule,
            "CREATIVE SPARK",
            "",
            f"  {briefing.spark.idea}",
            "",
            f"  Why now: {briefing.spark.rationale}",
            f"  Format:  {briefing.spark.format}",
            "  (an AI-generated suggestion, not reporting)",
            "",
        ])

    lines.extend([
        rule,
        f"{len(briefing.global_topics)} global and {len(briefing.niche_topics)} creative "
        f"trends from {briefing.source_count} sources.",
        "Melbourne Mama Morning Intelligence",
    ])

    return "\n".join(lines)
