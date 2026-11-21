"""Migawka treści strony: oferta (S10), kontakt (S03, wartości tylko jako skróty), cel CTA (E13)."""
from __future__ import annotations

import re
from html import unescape
from typing import Any, Mapping, Optional
from urllib.parse import urljoin, urlparse

from methadata.experiments.funnel.stages import CTA, analyze_html as funnel_stages
from methadata.sources.contact import adapter as _contact_adapter
from methadata.sources.contact.scan import parse_html as _contact_page, scan as _contact_scan
from methadata.sources.offer_id.scan import load_config as _offer_cfg, scan_html as _offer_scan

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
_PUB = [re.compile(p, re.I | re.S) for p in (
    r"""<meta[^>]+property=["']article:published_time["'][^>]+content=["']([^"']+)""",
    r"""<meta[^>]+content=["']([^"']+)["'][^>]+property=["']article:published_time["']""",
    r'"datePublished"\s*:\s*"([^"]+)"',
    r"""<time[^>]+datetime=["']([^"']+)["']""")]
_PRICE = re.compile(r"(\d[\d\s.,]{0,9})\s*(zł|pln|eur|€|usd|\$)", re.I)


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAGS.sub(" ", s))).strip()


def normalize_url(url: str) -> str:
    """Adres bez fragmentu i końcowego '/', host małymi literami; 'url:' z magazynu F02 jest zdejmowane."""
    u = (url or "").strip()
    if u.startswith("url:"):
        u = u[4:]
    p = urlparse(u)
    if not p.scheme or not p.netloc:
        return u
    path = p.path.rstrip("/") or ""
    return f"{p.scheme.lower()}://{p.netloc.lower()}{path}" + (f"?{p.query}" if p.query else "")


def cta_target(html: str, base_url: str = "") -> Optional[dict]:
    """Cel pierwszego wezwania do działania: {text, href, host}; None, gdy brak CTA lub href."""
    for st in funnel_stages(html):
        if st.kind != CTA:
            continue
        frag = html[st.start:st.end]
        m = _HREF.search(frag)
        text = _text(frag)[:80]
        if not m:
            return {"text": text, "href": None, "host": None}
        href = unescape(m.group(1)).strip()
        full = urljoin(base_url, href) if base_url else href
        host = urlparse(full).hostname
        return {"text": text, "href": normalize_url(full) if host else href, "host": host}
    return None


def analyze_html(html: str, url: str = "", *, contact_extractor=None,
                 offer_cfg: Optional[Mapping[str, Any]] = None) -> dict:
    """Migawka: offer {ids{kind:[...]}, title, price}, contacts [{key, masked, role}], cta, published_at.
    status: ok | no_data (brak jakiegokolwiek sygnału - nie wolno tego czytać jako 'puste' ani 'bez zmian')."""
    ce = contact_extractor or _contact_adapter.ContactExtractor()
    cfg = dict(offer_cfg) if offer_cfg else _offer_cfg()
    ids: dict[str, set] = {}
    for h in _offer_scan(html, url, cfg):
        ids.setdefault(h.kind, set()).add(h.value.strip() if h.kind != "gtin" else re.sub(r"\D", "", h.value))
    t = _TITLE.search(html) or _H1.search(html)
    h1 = _H1.search(html)
    title = _text((h1 or t).group(1)) if (h1 or t) else None
    pm = _PRICE.search(_text(html))
    price = re.sub(r"\s+", "", pm.group(1)).replace(",", ".") + " " + pm.group(2).lower() if pm else None
    contacts, seen = [], set()
    for h in _contact_scan(_contact_page(html), ce.cfg, "html"):
        k = (h.kind, ce._key(h))  # noqa: SLF001 - skrót wartości, nigdy surowy kontakt
        if k not in seen:
            seen.add(k)
            contacts.append({"key": k[1], "kind": h.kind, "masked": h.masked, "role": h.role})
    contacts.sort(key=lambda c: (c["kind"], c["key"]))
    pub = next((m.group(1) for p in _PUB if (m := p.search(html))), None)
    snap = {"offer": {"ids": {k: sorted(v) for k, v in sorted(ids.items())}, "title": title, "price": price},
            "contacts": contacts, "cta": cta_target(html, url), "published_at": pub}
    has = bool(ids or contacts or snap["cta"] or title)
    snap["status"] = "ok" if has else "no_data"
    return snap
