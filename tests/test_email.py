"""Phase 7: email rendering and delivery (PRD 42-47, 61, 62, 68, 69)."""

import datetime as dt
import re
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from app.ai.briefing import ResearchedTopic
from app.ai.schemas import BriefSource, SparkIdea
from app.errors import BriefingError, ErrorCode
from app.mailer import (
    Briefing,
    NullSender,
    ResendSender,
    build_mail_client,
    build_subject,
    render_html,
    render_text,
    require_send_failure,
    shortfall_note,
)

IST = ZoneInfo("Asia/Kolkata")
TODAY = dt.date(2026, 9, 1)
GENERATED = dt.datetime(2026, 9, 1, 7, 30, tzinfo=IST)


def topic(index=1, section="global", **overrides):
    from app.models import Section

    defaults = {
        "topic_id": f"t{index}",
        "section": Section.GLOBAL if section == "global" else Section.NICHE,
        "headline": f"Headline number {index}",
        "what_happened": f"Something happened, item {index}.",
        "why_trending": "It is being widely reported.",
        "why_it_matters": "It affects many people.",
        "trend_score": 80.0,
        "confidence": 0.9,
        "category": "world" if section == "global" else "filmmaking",
        "sources": [
            BriefSource(title="A report", url="https://reuters.com/a", publisher="Reuters"),
            BriefSource(title="Another", url="https://bbc.co.uk/b", publisher="BBC News"),
        ],
    }
    if section == "niche":
        defaults["creative_angle"] = "Make a short documentary about this."
    return ResearchedTopic(**{**defaults, **overrides})


def briefing(global_count=5, niche_count=5, spark=None, **overrides):
    defaults = {
        "briefing_date": TODAY,
        "timezone": "Asia/Kolkata",
        "global_topics": [topic(i, "global") for i in range(1, global_count + 1)],
        "niche_topics": [topic(i, "niche") for i in range(1, niche_count + 1)],
        "spark": spark,
        "generated_at": GENERATED,
    }
    return Briefing(**{**defaults, **overrides})


SPARK = SparkIdea(
    idea="Interview a Telugu short-film director about festival routes.",
    format="podcast",
    rationale="A Telugu short film was selected today.",
    confidence=0.8,
)


# --- subject and header (PRD 47) --------------------------------------------


@pytest.mark.unit
def test_subject_carries_the_date():
    assert build_subject(TODAY) == "🌅 Morning Intelligence — Sep 1, 2026"


@pytest.mark.unit
def test_header_renders_the_date_in_ist():
    """The briefing is timestamped in the operating timezone, not UTC."""
    html = render_html(briefing())

    assert "Tuesday, September 1, 2026" in html
    assert "7:30 AM IST" in html


# --- structure (PRD 42, 43) -------------------------------------------------


@pytest.mark.unit
def test_full_briefing_renders_both_sections_and_every_card():
    html = render_html(briefing(5, 5, SPARK))

    assert "Global Pulse" in html
    assert "Creative Radar" in html
    assert "Creative Spark" in html
    for index in range(1, 6):
        assert f"Headline number {index}" in html


@pytest.mark.unit
def test_cards_are_numbered():
    html = render_html(briefing(3, 0))

    for label in ("01", "02", "03"):
        assert label in html


@pytest.mark.unit
def test_every_card_carries_the_three_required_blocks():
    """PRD 43: what happened, why it is trending, why it matters."""
    html = render_html(briefing(1, 0))

    assert "What happened" in html
    assert "Why it is trending" in html
    assert "Why it matters" in html


@pytest.mark.unit
def test_creative_angle_appears_only_in_the_niche_section():
    """PRD 43: the creative angle is what distinguishes a niche card."""
    global_only = render_html(briefing(1, 0))
    with_niche = render_html(briefing(0, 1))

    assert "Creative angle" not in global_only
    assert "Creative angle" in with_niche


@pytest.mark.unit
def test_ai_suggestions_are_labelled_as_suggestions():
    """PRD 40: generated ideas must be distinguishable from factual reporting."""
    html = render_html(briefing(0, 1, SPARK))

    assert html.count("not reporting") >= 2


@pytest.mark.unit
def test_spark_section_is_omitted_when_there_is_no_idea():
    """PRD 41: the section is optional. Do not print an empty box."""
    assert "Creative Spark" not in render_html(briefing(5, 5, spark=None))


@pytest.mark.unit
def test_sources_are_linked_on_every_card():
    html = render_html(briefing(1, 0))

    assert 'href="https://reuters.com/a"' in html
    assert "Reuters" in html


@pytest.mark.unit
def test_conflicting_sources_are_surfaced_to_the_reader():
    """PRD 36: report the disagreement rather than hiding it."""
    conflicted = topic(1, "global", conflict_detected=True,
                       uncertainties=["Figures differ between X and Y"])
    html = render_html(briefing(0, 0, global_topics=[conflicted]))

    assert "Sources disagree" in html
    assert "Figures differ" in html


# --- partial briefings (PRD 62) ---------------------------------------------


@pytest.mark.unit
def test_partial_niche_section_says_so_plainly():
    """PRD 62: display that only four were available. Never invent a fifth."""
    html = render_html(briefing(5, 4))

    assert "4 verified creative trends were available today" in html
    assert "Nothing has been invented" in html


@pytest.mark.unit
def test_complete_briefing_shows_no_shortfall_note():
    assert "were available today" not in render_html(briefing(5, 5))


@pytest.mark.unit
@pytest.mark.parametrize(
    "count,expected_fragment",
    [
        (0, "No creative trends could be verified today."),
        (1, "1 verified creative trend was available today"),
        (4, "4 verified creative trends were available today"),
    ],
)
def test_shortfall_wording_matches_the_count(count, expected_fragment):
    assert expected_fragment in shortfall_note(count, 5, "creative")


@pytest.mark.unit
def test_no_shortfall_note_when_the_target_is_met():
    assert shortfall_note(5, 5, "creative") == ""


@pytest.mark.unit
def test_briefing_knows_it_is_partial():
    assert briefing(5, 4).is_partial is True
    assert briefing(5, 5).is_partial is False


# --- escaping and URL safety (PRD 68, 69) -----------------------------------


@pytest.mark.unit
def test_script_tag_in_a_headline_is_escaped():
    """PRD 69: model and feed text is never injected as markup.

    A feed can carry anything. This is the boundary that stops a headline becoming
    executable content in someone's inbox.
    """
    hostile = topic(1, "global", headline="<script>alert('xss')</script> Breaking")
    html = render_html(briefing(0, 0, global_topics=[hostile]))

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.unit
@pytest.mark.parametrize(
    "field",
    ["what_happened", "why_trending", "why_it_matters"],
)
def test_every_prose_field_is_escaped(field):
    hostile = topic(1, "global", **{field: "<img src=x onerror=alert(1)>"})
    html = render_html(briefing(0, 0, global_topics=[hostile]))

    assert "<img src=x" not in html
    assert "&lt;img" in html


@pytest.mark.unit
def test_creative_angle_is_escaped():
    hostile = topic(1, "niche", creative_angle="<script>bad()</script>")
    html = render_html(briefing(0, 0, niche_topics=[hostile]))

    assert "<script>bad()</script>" not in html


@pytest.mark.unit
def test_spark_text_is_escaped():
    hostile = SparkIdea(idea="<script>x</script>", format="podcast",
                        rationale="ok", confidence=0.9)
    html = render_html(briefing(1, 0, spark=hostile))

    assert "<script>x</script>" not in html


@pytest.mark.unit
def test_a_javascript_url_never_becomes_a_link():
    """PRD 68: only validated http/https URLs may appear as hrefs."""
    dangerous = topic(1, "global", sources=[
        BriefSource(title="Bad", url="javascript:alert(1)", publisher="Evil"),
        BriefSource(title="Good", url="https://reuters.com/a", publisher="Reuters"),
    ])
    html = render_html(briefing(0, 0, global_topics=[dangerous]))

    assert "javascript:" not in html
    assert 'href="https://reuters.com/a"' in html


@pytest.mark.unit
def test_a_quote_in_a_url_cannot_break_out_of_the_href_attribute():
    sneaky = topic(1, "global", sources=[
        BriefSource(title="X", url='https://e.com/a"onmouseover="alert(1)',
                    publisher="E"),
    ])
    html = render_html(briefing(0, 0, global_topics=[sneaky]))

    assert 'onmouseover="alert(1)"' not in html


# --- email client compatibility (PRD 44) ------------------------------------


@pytest.mark.unit
def test_no_javascript_anywhere_in_the_output():
    html = render_html(briefing(5, 5, SPARK))

    assert "<script" not in html.lower()
    assert "onclick" not in html.lower()


@pytest.mark.unit
def test_layout_uses_tables_for_outlook():
    """Outlook renders through Word, which has no flexbox or grid."""
    html = render_html(briefing(2, 2))

    assert html.count("<table") >= 4
    assert 'role="presentation"' in html


@pytest.mark.unit
def test_styles_are_inline_on_elements():
    """Gmail strips style blocks in some contexts and head entirely on forward."""
    html = render_html(briefing(1, 0))

    assert html.count('style="') > 20


@pytest.mark.unit
def test_a_responsive_breakpoint_is_present():
    assert "@media" in render_html(briefing(1, 0))


@pytest.mark.unit
def test_no_external_assets_are_referenced():
    html = render_html(briefing(5, 5, SPARK))

    assert "<img" not in html.lower()
    assert "url(http" not in html.lower()


# --- plain-text fallback (PRD 46) -------------------------------------------


@pytest.mark.unit
def test_plain_text_version_is_produced():
    text = render_text(briefing(5, 5, SPARK))

    assert text.strip()
    assert "MELBOURNE MAMA" in text
    assert "GLOBAL PULSE" in text
    assert "CREATIVE RADAR" in text
    assert "CREATIVE SPARK" in text


@pytest.mark.unit
def test_plain_text_contains_no_markup():
    text = render_text(briefing(3, 3, SPARK))

    assert not re.search(r"<[a-zA-Z/][^>]*>", text), "plain text must not contain tags"


@pytest.mark.unit
def test_plain_text_includes_every_source_url():
    text = render_text(briefing(1, 0))

    assert "https://reuters.com/a" in text
    assert "https://bbc.co.uk/b" in text


@pytest.mark.unit
def test_plain_text_omits_invalid_urls():
    dangerous = topic(1, "global", sources=[
        BriefSource(title="Bad", url="javascript:alert(1)", publisher="Evil"),
    ])

    assert "javascript:" not in render_text(briefing(0, 0, global_topics=[dangerous]))


@pytest.mark.unit
def test_plain_text_reports_a_partial_day():
    assert "NOTE:" in render_text(briefing(5, 3))


@pytest.mark.unit
def test_plain_text_labels_ai_suggestions():
    text = render_text(briefing(0, 1, SPARK))

    assert text.count("not reporting") >= 2


# --- delivery (PRD 61) ------------------------------------------------------

SEND_ARGS = {
    "sender": "brief@melbournemama.org",
    "recipient": "founder@example.com",
    "subject": "Test",
    "html": "<p>hi</p>",
    "text": "hi",
}


@pytest.mark.integration
@respx.mock
async def test_successful_send_returns_a_message_id():
    route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, json={"id": "msg_abc123"})
    )

    async with build_mail_client() as client:
        result = await ResendSender("key", backoff_seconds=0).send(client, **SEND_ARGS)

    assert result.ok is True
    assert result.message_id == "msg_abc123"
    assert route.call_count == 1


@pytest.mark.integration
@respx.mock
async def test_both_html_and_text_parts_are_sent():
    """PRD 46: the plain-text fallback must actually reach the provider."""
    route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, json={"id": "m"})
    )

    async with build_mail_client() as client:
        await ResendSender("key", backoff_seconds=0).send(client, **SEND_ARGS)

    import json

    body = json.loads(route.calls[0].request.content)
    assert body["html"] and body["text"]
    assert body["to"] == ["founder@example.com"]


@pytest.mark.integration
@respx.mock
async def test_transient_failure_is_retried_three_times_then_reported():
    """PRD 61: three attempts, then email_status=failed."""
    route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(500)
    )

    async with build_mail_client() as client:
        result = await ResendSender("key", backoff_seconds=0).send(client, **SEND_ARGS)

    assert result.ok is False
    assert route.call_count == 3


@pytest.mark.integration
@respx.mock
async def test_a_retry_that_succeeds_is_reported_as_sent():
    respx.post("https://api.resend.com/emails").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"id": "m"})]
    )

    async with build_mail_client() as client:
        result = await ResendSender("key", backoff_seconds=0).send(client, **SEND_ARGS)

    assert result.ok is True
    assert result.attempts == 2


@pytest.mark.integration
@respx.mock
async def test_unverified_sending_domain_is_not_retried():
    """Resend returns 403 for an unverified domain, and will do so every time."""
    route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(403, json={"message": "domain is not verified"})
    )

    async with build_mail_client() as client:
        result = await ResendSender("key", backoff_seconds=0).send(client, **SEND_ARGS)

    assert result.ok is False
    assert "not verified" in result.error
    assert route.call_count == 1


@pytest.mark.integration
@respx.mock
async def test_network_errors_are_retried():
    route = respx.post("https://api.resend.com/emails").mock(
        side_effect=httpx.ConnectError("refused")
    )

    async with build_mail_client() as client:
        result = await ResendSender("key", backoff_seconds=0).send(client, **SEND_ARGS)

    assert result.ok is False
    assert route.call_count == 3


@pytest.mark.unit
def test_failed_delivery_raises_so_the_run_is_not_marked_successful():
    from app.mailer.sender import SendResult

    with pytest.raises(BriefingError) as caught:
        require_send_failure(SendResult(ok=False, error="HTTP 500"))

    assert caught.value.code == ErrorCode.EMAIL_FAILED


@pytest.mark.unit
def test_successful_delivery_raises_nothing():
    from app.mailer.sender import SendResult

    require_send_failure(SendResult(ok=True, message_id="m"))


@pytest.mark.integration
@respx.mock
async def test_dry_run_sender_sends_nothing():
    """PRD 86: a dry run generates everything but must not deliver."""
    route = respx.post("https://api.resend.com/emails").mock(
        return_value=httpx.Response(200, json={"id": "should-not-happen"})
    )
    sender = NullSender()

    async with build_mail_client() as client:
        result = await sender.send(client, **SEND_ARGS)

    assert result.ok is True
    assert route.call_count == 0, "a dry run must not contact the provider"
    assert sender.last_html == "<p>hi</p>"


@pytest.mark.unit
def test_the_api_key_never_appears_in_a_result():
    from app.mailer.sender import SendResult

    assert "key" not in SendResult(ok=False, error="HTTP 500: bad").error.replace("bad", "")


# --- SendGrid adapter (PRD 6, 46, 61) ---------------------------------------

SENDGRID_ENDPOINT = "https://api.sendgrid.com/v3/mail/send"


@pytest.mark.unit
@pytest.mark.parametrize(
    "value,expected",
    [
        ("Melbourne Mama <brief@example.org>", ("Melbourne Mama", "brief@example.org")),
        ("<brief@example.org>", ("", "brief@example.org")),
        ("brief@example.org", ("", "brief@example.org")),
        ("  Spaced Name  < a@b.com >  ", ("Spaced Name", "a@b.com")),
    ],
)
def test_display_names_are_split_for_sendgrid(value, expected):
    """Resend takes one combined field; SendGrid rejects it and wants them apart.
    Configuration should not have to know which provider is in use."""
    from app.mailer.sender import split_address

    assert split_address(value) == expected


@pytest.mark.integration
@respx.mock
async def test_sendgrid_accepts_a_202_as_success():
    """SendGrid answers with 202 Accepted and an empty body, not 200."""
    from app.mailer import SendGridSender

    route = respx.post(SENDGRID_ENDPOINT).mock(
        return_value=httpx.Response(202, headers={"X-Message-Id": "sg_abc123"})
    )

    async with build_mail_client() as client:
        result = await SendGridSender("key", backoff_seconds=0).send(client, **SEND_ARGS)

    assert result.ok is True
    assert result.message_id == "sg_abc123"
    assert route.call_count == 1


@pytest.mark.integration
@respx.mock
async def test_sendgrid_payload_has_plain_text_before_html():
    """SendGrid builds the MIME parts in the order given, and the plain-text
    alternative must come first or clients show the wrong one."""
    import json

    from app.mailer import SendGridSender

    route = respx.post(SENDGRID_ENDPOINT).mock(return_value=httpx.Response(202))

    async with build_mail_client() as client:
        await SendGridSender("key", backoff_seconds=0).send(client, **SEND_ARGS)

    body = json.loads(route.calls[0].request.content)
    assert [part["type"] for part in body["content"]] == ["text/plain", "text/html"]
    assert body["personalizations"][0]["to"][0]["email"] == "founder@example.com"


@pytest.mark.integration
@respx.mock
async def test_sendgrid_splits_the_display_name_into_its_own_field():
    import json

    from app.mailer import SendGridSender

    route = respx.post(SENDGRID_ENDPOINT).mock(return_value=httpx.Response(202))
    args = {**SEND_ARGS, "sender": "Melbourne Mama <brief@example.org>"}

    async with build_mail_client() as client:
        await SendGridSender("key", backoff_seconds=0).send(client, **args)

    body = json.loads(route.calls[0].request.content)
    assert body["from"] == {"email": "brief@example.org", "name": "Melbourne Mama"}


@pytest.mark.integration
@respx.mock
async def test_sendgrid_unverified_sender_is_not_retried():
    """An unverified single sender returns 403 and will do so every time."""
    from app.mailer import SendGridSender

    route = respx.post(SENDGRID_ENDPOINT).mock(
        return_value=httpx.Response(403, json={"errors": [{"message": "sender not verified"}]})
    )

    async with build_mail_client() as client:
        result = await SendGridSender("key", backoff_seconds=0).send(client, **SEND_ARGS)

    assert result.ok is False
    assert "not verified" in result.error
    assert route.call_count == 1


@pytest.mark.integration
@respx.mock
async def test_sendgrid_transient_failure_is_retried_three_times():
    from app.mailer import SendGridSender

    route = respx.post(SENDGRID_ENDPOINT).mock(return_value=httpx.Response(503))

    async with build_mail_client() as client:
        result = await SendGridSender("key", backoff_seconds=0).send(client, **SEND_ARGS)

    assert result.ok is False
    assert route.call_count == 3


@pytest.mark.integration
@respx.mock
async def test_sendgrid_200_is_not_mistaken_for_success():
    """Only 202 means accepted. Treating any 2xx as success would report a delivery
    that never happened."""
    from app.mailer import SendGridSender

    respx.post(SENDGRID_ENDPOINT).mock(return_value=httpx.Response(200, text="unexpected"))

    async with build_mail_client() as client:
        result = await SendGridSender("key", attempts=1, backoff_seconds=0).send(
            client, **SEND_ARGS
        )

    assert result.ok is False


@pytest.mark.unit
def test_configuration_chooses_the_provider(configured_env):
    """PRD 6: one provider per run, selected by config rather than by import."""
    from app.config import get_settings
    from app.deps import _build_sender
    from app.mailer import ResendSender, SendGridSender

    configured_env.setenv("EMAIL_API_KEY", "test-key")
    for provider, expected in (("sendgrid", SendGridSender), ("resend", ResendSender)):
        configured_env.setenv("EMAIL_PROVIDER", provider)
        get_settings.cache_clear()
        import logging

        assert isinstance(_build_sender(get_settings(), logging.getLogger()), expected)


@pytest.mark.unit
def test_an_unknown_provider_is_rejected_at_startup(configured_env):
    from pydantic import ValidationError

    from app.config import Settings

    configured_env.setenv("EMAIL_PROVIDER", "mailchimp")

    with pytest.raises(ValidationError):
        Settings()
