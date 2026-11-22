"""Publiczne wzbogacanie domen: DNS (IP), certyfikat TLS i RDAP (rejestrator, wiek domeny, NS) z cache na dysku."""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import ssl
from pathlib import Path
from typing import Any, Iterable

from .errors import safe_text
from .storage import atomic_write_json, read_json

log = logging.getLogger(__name__)

# Domyślne popularne CDN/trackery, których nie warto wzbogacać pod kątem „wspólnej infrastruktury oszusta”.
IGNORED_COMMON_HOSTS = {
    "code.jquery.com", "fonts.googleapis.com", "fonts.gstatic.com", "cdnjs.cloudflare.com",
    "connect.facebook.net", "www.googletagmanager.com", "www.google-analytics.com",
    "ajax.googleapis.com", "cdn.jsdelivr.net", "unpkg.com",
}


_HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _is_public(ip: str) -> bool:
    """Tylko adresy globalne: bez loopback, sieci prywatnych, link-local (np. 169.254.169.254) i zarezerwowanych."""
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def _registrable_domain(host: str) -> str:
    parts = [p for p in host.lower().strip(".").split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if parts[-1] in {"uk", "pl", "au", "br", "jp"} and parts[-2] in {"co", "com", "org", "net", "gov", "edu"} and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _dns_ips(host: str, timeout: float = 2.0) -> list[str]:
    prev = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return sorted({info[4][0] for info in infos if info and info[4]})[:4]
    except OSError:
        return []
    finally:
        socket.setdefaulttimeout(prev)


def _tls_info(host: str, ip: str, timeout: float = 2.5) -> dict[str, str]:
    try:
        ctx = ssl.create_default_context()
        # Łączymy się ze sprawdzonym adresem IP (nie z nazwą), więc zmiana odpowiedzi DNS po sprawdzeniu nic nie daje.
        with socket.create_connection((ip, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert() or {}
        issuer_parts = dict(item[0] for item in cert.get("issuer", ()) if item)
        issuer = issuer_parts.get("organizationName") or issuer_parts.get("commonName") or ""
        return {
            "issuer": safe_text(issuer, 120),
            "not_before": safe_text(str(cert.get("notBefore", "")), 64),
            "not_after": safe_text(str(cert.get("notAfter", "")), 64),
        }
    except Exception:  # noqa: BLE001
        return {}


def _rdap_info(domain: str, timeout: float = 3.0) -> dict[str, Any]:
    try:
        import httpx
    except ImportError:
        return {}
    try:
        resp = httpx.get(
            f"https://rdap.org/domain/{domain}",
            headers={"Accept": "application/rdap+json, application/json"},
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=2.0),
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
        created = ""
        for ev in data.get("events", ()):
            if ev.get("eventAction") in {"registration", "registered"} and ev.get("eventDate"):
                created = safe_text(str(ev["eventDate"]), 40)
                break
        registrar = ""
        for ent in data.get("entities", ()):
            if "registrar" in (ent.get("roles") or ()):
                vcard = ent.get("vcardArray")
                if isinstance(vcard, list) and len(vcard) >= 2:
                    for field in vcard[1]:
                        if isinstance(field, list) and field and field[0] == "fn" and len(field) >= 4:
                            registrar = safe_text(str(field[3]), 120)
                            break
                if registrar:
                    break
        nameservers = [
            safe_text(str(ns.get("ldhName", "")).lower(), 120)
            for ns in data.get("nameservers", ())
            if ns.get("ldhName")
        ][:4]
        return {"registrar": registrar, "created": created, "nameservers": nameservers}
    except Exception:  # noqa: BLE001
        return {}


def enrich_domains(hosts: Iterable[str], cache_path: Path, *, max_new: int = 8) -> dict[str, dict[str, Any]]:
    """Zwraca słownik {host: {ips, tls, registrar, created, nameservers}} z trwałym cache."""
    if os.environ.get("METHADATA_DISABLE_ENRICHMENT") == "1":
        return {}
    clean = sorted({h.lower().strip(".") for h in hosts if h and h.lower() not in IGNORED_COMMON_HOSTS})
    if not clean:
        return {}

    cache: dict[str, dict[str, Any]] = read_json(cache_path) or {}
    dirty = False
    queried = 0
    out: dict[str, dict[str, Any]] = {}

    for host in clean:
        if host in cache:
            out[host] = cache[host]
            continue
        if not _HOSTNAME.match(host):
            continue  # adresy IP, nazwy lokalne i śmieci z importowanych danych nie są wzbogacane
        if queried >= max_new:
            continue
        queried += 1
        ips = _dns_ips(host)
        if ips and not all(_is_public(ip) for ip in ips):
            # Nazwa wskazuje na sieć prywatną/loopback: żadnych zapytań wychodzących, tylko pusty wpis w cache.
            cache[host] = {"ips": [], "tls_issuer": "", "tls_not_before": "", "registrar": "", "created": "", "nameservers": []}
            dirty = True
            continue
        tls = _tls_info(host, ips[0]) if ips else {}
        rdap = _rdap_info(_registrable_domain(host))
        record = {
            "ips": ips,
            "tls_issuer": tls.get("issuer", ""),
            "tls_not_before": tls.get("not_before", ""),
            "registrar": rdap.get("registrar", ""),
            "created": rdap.get("created", ""),
            "nameservers": rdap.get("nameservers", []),
        }
        cache[host] = record
        out[host] = record
        dirty = True

    if dirty:
        try:
            atomic_write_json(cache_path, cache)
        except OSError:
            pass
    return out
