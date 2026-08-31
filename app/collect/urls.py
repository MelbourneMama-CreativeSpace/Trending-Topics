"""URL validation and canonicalisation (PRD 24 level 1, PRD 68).

Two jobs:

* **Validation** -- decide whether a URL may enter the pipeline at all. Retrieved
  content is untrusted, so the scheme allowlist and the private-address block are a
  security boundary, not a tidiness measure.
* **Canonicalisation** -- collapse the many URLs that point at one story into a single
  key. This is level 1 of deduplication; levels 2 and 3 arrive in Phase 4.
"""

import ipaddress
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Campaign and referrer parameters. Present or absent, the article is the same one.
TRACKING_PARAM_PREFIXES = ("utm_", "pk_", "mtm_", "ga_", "hsa_", "vero_")
TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "twclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "ref",
        "referrer",
        "source",
        "cmpid",
        "ncid",
        "spm",
        "at_medium",
        "at_campaign",
        "smid",
        "_hsenc",
        "_hsmi",
        "icid",
        "ito",
        "CMP",
    }
)

_BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})
_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localdomain")

DEFAULT_PORTS = {"http": 80, "https": 443}


def is_valid_url(url: str) -> bool:
    """True if this URL may be fetched and may appear in the email.

    Rejects the `javascript:`, `file:` and `data:` schemes named in PRD 68, plus
    loopback and private addresses -- a model or a feed can suggest an internal
    address, and following one turns this agent into an SSRF vector.
    """
    if not url or not isinstance(url, str) or len(url) > 2048:
        return False

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return False
    if not parts.netloc or not parts.hostname:
        return False

    return not _is_private_host(parts.hostname)


def _is_private_host(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")

    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return True

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False  # a normal domain name

    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def canonical_url(url: str) -> str:
    """Collapse cosmetic differences so one story maps to one key.

    Lowercases scheme and host, drops `www.`, the fragment, tracking parameters and
    default ports, and normalises the trailing slash. Remaining query parameters are
    sorted, because feeds and search results order them differently for the same page.
    """
    parts = urlsplit(url.strip())

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    # www.example.com/x and example.com/x are the same page; keeping both would let a
    # duplicate straight through level 1.
    if host.startswith("www."):
        host = host[4:]

    netloc = host
    if parts.port and parts.port != DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{parts.port}"

    path = parts.path.rstrip("/") or "/"

    kept = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(key)
    )

    return urlunsplit((scheme, netloc, path, urlencode(kept), ""))


def _is_tracking_param(key: str) -> bool:
    lowered = key.lower()
    return lowered in {p.lower() for p in TRACKING_PARAMS} or lowered.startswith(
        TRACKING_PARAM_PREFIXES
    )


def domain_of(url: str) -> str:
    """Registrable-ish host, with a leading `www.` removed."""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host
