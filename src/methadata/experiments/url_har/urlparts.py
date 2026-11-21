"""Rozkład URL na host, wzorzec ścieżki i parametry (tylko cechy dostępne w samym adresie)."""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import parse_qsl, unquote, urlsplit

from methadata.contracts.urls import MASKED, SENSITIVE_PARAMS

# Parametry i hosty, których zgodność nie świadczy o kampanii (wspólne dla wszystkich reklam danej platformy).
GENERIC_PARAMS = frozenset({"gad_source", "gad_campaignid", "utm_source", "utm_medium", "ref", "fbclid"}) | SENSITIVE_PARAMS
GENERIC_HOST_SUFFIXES = (
    "google-analytics.com", "analytics.google.com", "googletagmanager.com", "doubleclick.net", "googleadservices.com",
    "google.com", "gstatic.com", "googlesyndication.com", "facebook.net", "facebook.com", "connect.facebook.net",
    "tiktok.com", "hotjar.com", "clarity.ms", "taboola.com", "outbrain.com", "yandex.ru", "cloudflare.com",
    "jsdelivr.net", "cdnjs.cloudflare.com", "googleapis.com", "bing.com",
)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX = re.compile(r"^[0-9a-f]{16,}$", re.I)
_INT = re.compile(r"^\d+$")
_TOKEN = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9_\-]{12,}$")


def is_generic_host(host: Optional[str]) -> bool:
    h = (host or "").lower()
    return any(h == s or h.endswith("." + s) for s in GENERIC_HOST_SUFFIXES)


def segment_pattern(seg: str) -> str:
    if _UUID.match(seg):
        return "{uuid}"
    if _INT.match(seg):
        return "{int}"
    if _HEX.match(seg):
        return "{hex}"
    if _TOKEN.match(seg):
        return "{token}"
    return seg.lower()


def path_pattern(path: str) -> str:
    segs = [s for s in path.split("/") if s]
    return "/" + "/".join(segment_pattern(unquote(s)) for s in segs)


def parse_url(url: str) -> Optional[dict[str, Any]]:
    """Cechy URL: host, path, path_pattern, param_keys (w kolejności), params (klucz -> wartości; wartości
    identyfikatorów kliknięcia zamaskowane). None dla adresu bez hosta."""
    try:
        p = urlsplit(url.strip())
        host = (p.hostname or "").lower()
        pairs = parse_qsl(p.query, keep_blank_values=True)
    except ValueError:
        return None
    if not host:
        return None
    params: dict[str, list[str]] = {}
    for k, v in pairs:
        params.setdefault(k, []).append(MASKED if k.lower() in SENSITIVE_PARAMS else v)
    return {"host": host, "path": p.path or "/", "path_pattern": path_pattern(p.path or "/"),
            "param_keys": list(params), "params": params}
