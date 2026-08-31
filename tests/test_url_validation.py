"""Phase 3: URL validation and canonicalisation (PRD 24, 68)."""

import pytest

from app.collect.urls import canonical_url, domain_of, is_valid_url


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/story",
        "http://example.com/story",
        "https://sub.example.co.uk/a/b?id=1",
        "https://news.google.com/rss/search?q=%E0%B0%A4%E0%B1%86",
    ],
)
def test_http_and_https_are_allowed(url):
    assert is_valid_url(url) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(document.cookie)",
        "JavaScript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "mailto:someone@example.com",
    ],
)
def test_dangerous_schemes_are_rejected(url):
    """PRD 68 names these explicitly. A model or a feed can propose any of them."""
    assert is_valid_url(url) is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://localhost:8000/api",
        "http://127.0.0.1/x",
        "http://[::1]/x",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/router",
        "http://172.16.0.1/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://service.internal/x",
        "http://printer.local/x",
    ],
)
def test_private_and_loopback_addresses_are_rejected(url):
    """Following a feed-supplied internal address would make this agent an SSRF vector.

    169.254.169.254 is the cloud metadata endpoint -- the highest-value target.
    """
    assert is_valid_url(url) is False


@pytest.mark.unit
@pytest.mark.parametrize("url", ["", "   ", "not a url", "https://", "http:///path", None])
def test_malformed_urls_are_rejected(url):
    assert is_valid_url(url) is False


@pytest.mark.unit
def test_absurdly_long_url_is_rejected():
    assert is_valid_url("https://example.com/" + "a" * 3000) is False


@pytest.mark.unit
def test_tracking_parameters_are_stripped():
    """utm_* and friends differ per share; the article behind them does not."""
    url = "https://example.com/story?utm_source=twitter&utm_medium=social&id=42&fbclid=abc"

    assert canonical_url(url) == "https://example.com/story?id=42"


@pytest.mark.unit
def test_canonicalisation_collapses_cosmetic_variants():
    """Every one of these is the same page and must produce one key."""
    variants = [
        "https://www.example.com/story",
        "https://example.com/story",
        "https://example.com/story/",
        "HTTPS://EXAMPLE.COM/story",
        "https://example.com:443/story",
        "https://example.com/story#comments",
        "https://example.com/story?utm_campaign=x",
    ]

    assert len({canonical_url(v) for v in variants}) == 1


@pytest.mark.unit
def test_meaningful_query_parameters_are_preserved():
    """Some sites put the article id in the query. Dropping it merges distinct stories."""
    assert canonical_url("https://example.com/a?id=1") != canonical_url(
        "https://example.com/a?id=2"
    )


@pytest.mark.unit
def test_query_parameter_order_does_not_matter():
    assert canonical_url("https://e.com/a?b=2&a=1") == canonical_url("https://e.com/a?a=1&b=2")


@pytest.mark.unit
def test_non_default_port_is_preserved():
    assert ":8080" in canonical_url("https://example.com:8080/story")


@pytest.mark.unit
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.theguardian.com/world/rss", "theguardian.com"),
        ("https://feeds.bbci.co.uk/news/rss.xml", "feeds.bbci.co.uk"),
        ("HTTPS://WWW.Example.COM/a", "example.com"),
    ],
)
def test_domain_extraction(url, expected):
    assert domain_of(url) == expected
