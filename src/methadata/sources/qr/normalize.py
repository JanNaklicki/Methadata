"""Klasyfikacja i normalizacja zawartości kodu QR do wartości porównywalnej (bez sieci)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

_CFG = Path(__file__).with_name("config.json")


def load_config() -> dict:
    return json.loads(_CFG.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Target:
    type: str                 # url | wifi | email | phone | sms | geo | payment | vcard | text
    value: str                # znormalizowana wartość do porównania
    host: Optional[str] = None
    path: Optional[str] = None    # host + ścieżka bez zapytania (tylko url)
    shortener: bool = False


_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24}(?::\d+)?(?:[/?#]\S*)?$", re.I)
_PAYMENT = ("bitcoin:", "ethereum:", "litecoin:", "monero:", "upi:", "bitcoincash:", "tron:")


def _host_matches(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def normalize_url(raw: str, cfg: Mapping[str, Any]) -> Optional[Target]:
    s = raw.strip()
    if "://" not in s:
        if not _DOMAIN_RE.match(s):
            return None
        s = "http://" + s
    try:
        u = urlsplit(s)
        host = (u.hostname or "").rstrip(".").lower()
        port = u.port
    except ValueError:
        return None
    if u.scheme.lower() not in ("http", "https") or not host:
        return None
    try:
        host = host.encode("ascii").decode("ascii") if host.isascii() else host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    if host.startswith("www."):
        host = host[4:]
    path = unquote(u.path or "").rstrip("/") or ""
    drop = set(cfg["tracking_params"])
    pref = tuple(cfg["tracking_prefixes"])
    q = sorted((k.lower(), v) for k, v in parse_qsl(u.query, keep_blank_values=True)
               if k.lower() not in drop and not k.lower().startswith(pref))
    hp = host + (f":{port}" if port and port not in (80, 443) else "")
    base = hp + path
    value = base + ("?" + urlencode(q) if q else "")
    return Target("url", value, host, base, _host_matches(host, cfg["shorteners"]))


def classify(payload: str, cfg: Optional[Mapping[str, Any]] = None) -> Optional[Target]:
    """None gdy pusta zawartość. Nigdy nie wykonuje ani nie pobiera celu."""
    cfg = cfg or load_config()
    s = (payload or "").strip()
    if not s:
        return None
    low = s.casefold()
    if low.startswith("wifi:"):
        m = re.search(r"S:((?:\\.|[^;])*)", s, re.I)
        return Target("wifi", "wifi:" + (m.group(1).casefold() if m else ""))
    if low.startswith("mailto:") or re.fullmatch(r"matmsg:.*", low):
        addr = re.sub(r"^mailto:", "", low).split("?")[0] if low.startswith("mailto:") else \
            (re.search(r"to:([^;]+)", low) or [None, ""])[1]
        return Target("email", "mailto:" + addr)
    if low.startswith("tel:"):
        return Target("phone", "tel:" + re.sub(r"[^\d+]", "", s[4:]))
    if low.startswith(("sms:", "smsto:")):
        return Target("sms", "sms:" + re.sub(r"[^\d+]", "", s.split(":", 1)[1].split("?")[0].split(":")[0]))
    if low.startswith("geo:"):
        return Target("geo", "geo:" + low[4:].split("?")[0])
    if low.startswith(_PAYMENT):
        scheme, rest = low.split(":", 1)
        return Target("payment", f"{scheme}:{rest.split('?')[0]}")
    if low.startswith(("begin:vcard", "mecard:")):
        return Target("vcard", "vcard:" + re.sub(r"\s+", " ", low)[:200])
    t = normalize_url(s, cfg)
    if t:
        return t
    return Target("text", re.sub(r"\s+", " ", low))
