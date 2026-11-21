"""Analiza URL jako tekstu (offline) i walidacja celu pobrania (SSRF)."""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlsplit

TRACKING_KEYS = {"gclid", "fbclid", "msclkid", "ttclid", "gad_source", "gad_campaignid", "adset_id", "ad_id",
                 "aatid", "opt_id", "from", "utm_source", "utm_medium", "utm_campaign", "utm_content",
                 "utm_term", "utm_origin"}

Resolver = Callable[[str, int], list[str]]


class BlockedTarget(Exception):
    """Cel pobrania niedozwolony (schemat, dane logowania, adres prywatny/lokalny)."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def analyze_url(url: str) -> dict:
    """Czysto tekstowa analiza URL, bez sieci i DNS."""
    raw = url.strip()
    out: dict = {"input": raw, "valid": False, "flags": []}
    try:
        p = urlsplit(raw)
        host = (p.hostname or "").lower()
        port = p.port
    except ValueError as e:
        out["flags"].append(f"parse_error:{e}")
        return out
    if p.scheme.lower() not in ("http", "https") or not host:
        out["flags"].append("not_http_url")
        return out
    out.update(valid=True, scheme=p.scheme.lower(), host=host, port=port, path=p.path or "/",
               has_userinfo=bool(p.username or p.password))
    try:
        ipaddress.ip_address(host)
        out["host_is_ip"] = True
        out["flags"].append("ip_host")
    except ValueError:
        out["host_is_ip"] = False
    labels = host.split(".")
    out["tld"] = labels[-1] if len(labels) > 1 else None
    out["registered_hint"] = ".".join(labels[-2:]) if len(labels) > 1 else host
    if any(l.startswith("xn--") for l in labels):
        out["flags"].append("punycode")
        try:
            out["host_unicode"] = host.encode("ascii").decode("idna")
        except UnicodeError:
            pass
    if not host.isascii():
        out["flags"].append("non_ascii_host")
    if out["has_userinfo"]:
        out["flags"].append("userinfo")
    q = parse_qsl(p.query, keep_blank_values=True)
    out["query_keys"] = sorted({k for k, _ in q})
    out["tracking_keys"] = sorted({k for k, _ in q if k.lower() in TRACKING_KEYS})
    if len(labels) > 4:
        out["flags"].append("many_subdomains")
    return out


def default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


def ip_allowed(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%")[0])
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


def check_target(url: str, *, allow_private: bool = False, resolver: Optional[Resolver] = None) -> tuple[str, int, str, str]:
    """Zwraca (host, port, ip_do_połączenia, schemat) albo rzuca BlockedTarget. Wszystkie adresy hosta muszą być publiczne."""
    try:
        p = urlsplit(url.strip())
        host, port = p.hostname, p.port
    except ValueError as e:
        raise BlockedTarget("invalid_url", str(e))
    scheme = p.scheme.lower()
    if scheme not in ("http", "https"):
        raise BlockedTarget("scheme_not_allowed", f"schemat {scheme!r} niedozwolony")
    if not host:
        raise BlockedTarget("invalid_url", "brak hosta")
    if p.username or p.password:
        raise BlockedTarget("userinfo_not_allowed", "dane logowania w URL")
    port = port or (443 if scheme == "https" else 80)
    try:
        ips = (resolver or default_resolver)(host, port)
    except (OSError, UnicodeError) as e:
        raise BlockedTarget("dns_error", f"nie rozwiązano {host}: {e}")
    if not ips:
        raise BlockedTarget("dns_error", f"brak adresów dla {host}")
    if not allow_private:
        bad = [ip for ip in ips if not ip_allowed(ip)]
        if bad:
            raise BlockedTarget("blocked_address", f"adres niepubliczny: {bad[0]}")
    return host, port, ips[0], scheme
