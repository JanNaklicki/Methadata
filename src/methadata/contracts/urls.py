"""Maskowanie identyfikatorów kliknięcia w adresach pokazywanych człowiekowi."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MASKED = "***"
SENSITIVE_PARAMS = frozenset({
    "gclid", "gbraid", "wbraid", "fbclid", "msclkid", "ttclid", "dclid", "twclid",
    "gad_campaignid", "click_id", "clickid", "token", "session", "sessionid", "sid", "auth", "key",
})


def mask_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    q = [(k, MASKED if k.lower() in SENSITIVE_PARAMS else v)
         for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q, safe="*"), parts.fragment))
