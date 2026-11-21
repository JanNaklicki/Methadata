"""HAR: udokumentowane przekierowania 3xx i identyfikatory integracji z kontekstem dostawcy."""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlsplit

from methadata.extractors.network.extractor import _san, parse_redirect_chains
from methadata.contracts.urls import mask_url

from .urlparts import is_generic_host

_GTM = re.compile(r"^(GTM|G|AW|UA|DC|GT)-[A-Z0-9\-]+$", re.I)
_VENDOR_BY_PREFIX = {"GTM": ("google_tag_manager", "container"), "G": ("google_analytics_4", "measurement"),
                     "AW": ("google_ads", "conversion"), "UA": ("google_analytics_ua", "property"),
                     "DC": ("google_floodlight", "advertiser"), "GT": ("google_tag", "tag")}


def _q(url_parts, name: str) -> Optional[str]:
    v = parse_qs(url_parts.query).get(name)
    return v[0] if v else None


def detect_integrations(url: str) -> list[dict[str, str]]:
    """Identyfikatory integracji rozpoznane z adresu żądania. Zwraca [{vendor, id_type, id, host}].
    Sama obecność biblioteki (np. fbevents.js bez identyfikatora) nic nie zwraca."""
    try:
        p = urlsplit(url)
        host, path = (p.hostname or "").lower(), p.path
    except ValueError:
        return []
    out: list[tuple[str, str, str]] = []
    h = lambda s: host == s or host.endswith("." + s)  # noqa: E731
    if h("googletagmanager.com"):
        i = _q(p, "id")
        m = _GTM.match(i or "")
        if m:
            vendor, typ = _VENDOR_BY_PREFIX[m.group(1).upper()]
            out.append((vendor, typ, i))
    elif h("google-analytics.com") or h("analytics.google.com"):
        i = _q(p, "tid") or _q(p, "id")
        if i:
            out.append(("google_analytics", "property", i))
    elif h("doubleclick.net") or h("googleadservices.com"):
        m = re.search(r"/(?:pagead/conversion|pagead/viewthroughconversion)/(\d+)", path)
        if m:
            out.append(("google_ads", "conversion", "AW-" + m.group(1)))
    elif h("facebook.com") and path.startswith("/tr"):
        if (i := _q(p, "id")) and i.isdigit():
            out.append(("meta_pixel", "pixel", i))
    elif h("facebook.net"):
        m = re.match(r"^/signals/config/(\d+)", path)
        if m:
            out.append(("meta_pixel", "pixel", m.group(1)))
    elif h("tiktok.com"):
        if i := _q(p, "sdkid") or _q(p, "sdk_id"):
            out.append(("tiktok_pixel", "pixel", i))
    elif h("hotjar.com"):
        m = re.search(r"hotjar-(\d+)\.js", path)
        if m:
            out.append(("hotjar", "site", m.group(1)))
    elif h("clarity.ms"):
        m = re.match(r"^/tag/([a-z0-9]+)", path, re.I)
        if m:
            out.append(("microsoft_clarity", "project", m.group(1)))
    elif h("yandex.ru") or h("yandex.com"):
        m = re.match(r"^/watch/(\d+)", path)
        if m:
            out.append(("yandex_metrika", "counter", m.group(1)))
    elif h("taboola.com"):
        m = re.match(r"^/(?:libtrc/)?([A-Za-z0-9\-_]+?)(?:/loader\.js|/log|\.js)", path)
        if m and m.group(1) not in {"libtrc", "log"}:
            out.append(("taboola", "publisher", m.group(1)))
    return [{"vendor": v, "id_type": t, "id": i, "host": host} for v, t, i in out]


def har_entries(doc: Any) -> list[dict]:
    entries = doc["log"]["entries"]
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise ValueError("log.entries nie jest listą obiektów")
    return entries


def _host(url: str) -> Optional[str]:
    try:
        return (urlsplit(url).hostname or "").lower() or None
    except ValueError:
        return None


def redirect_features(entries: list[dict]) -> list[dict[str, Any]]:
    """Łańcuchy z jawnych 3xx (Location/redirectURL). Samo uporządkowanie żądań nie tworzy łańcucha.
    Każdy: {hops:[{index,status,url,host,observed}], hosts:[...], complete, indices:[...]}."""
    out = []
    for chain in parse_redirect_chains(entries):
        hops = [{"index": c["index"], "status": c["status"], "url": _san(c["url"]), "host": _host(c["url"]),
                 "observed": c["observed"]} for c in chain]
        if len(hops) < 2:
            continue
        out.append({"hops": hops, "hosts": hosts_in_order([h["host"] for h in hops]),
                    "complete": all(h["observed"] for h in hops),
                    "indices": [h["index"] for h in hops if h["index"] is not None]})
    return out


def hosts_in_order(hosts: Iterable[Optional[str]]) -> list[str]:
    out: list[str] = []
    for h in hosts:
        if h and (not out or out[-1] != h):
            out.append(h)
    return out


def integration_features(entries: list[dict]) -> list[dict[str, Any]]:
    """Unikalne (vendor,id) z pierwszym wpisem HAR, w którym wystąpiły."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for i, e in enumerate(entries):
        url = (e.get("request") or {}).get("url") or ""
        for d in detect_integrations(url):
            seen.setdefault((d["vendor"], d["id"]), {**d, "index": i, "request_url": mask_url(url)[:200]})
    return [seen[k] for k in sorted(seen)]


def first_party_host_order(entries: list[dict]) -> list[tuple[str, int]]:
    """Hosty niebędące bibliotekami śledzącymi w kolejności pierwszego żądania: [(host, indeks wpisu)]."""
    out: dict[str, int] = {}
    for i, e in enumerate(entries):
        h = _host((e.get("request") or {}).get("url") or "")
        if h and not is_generic_host(h):
            out.setdefault(h, i)
    return list(out.items())
