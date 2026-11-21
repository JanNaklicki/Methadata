"""Odczyt GTIN / SKU / ID oferty / sprzedawcy z HTML (JSON-LD, microdata, meta, URL, etykiety) i tekstu OCR."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

_CFG = Path(__file__).with_name("config.json")


def load_config() -> dict:
    return json.loads(_CFG.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Hit:
    kind: str            # gtin | sku | offer_id | seller_id
    value: str
    source: str          # jsonld | microdata | meta | url | label | ocr
    raw: str = ""
    marketplace: Optional[str] = None
    seller: Optional[str] = None   # dla sku: wystawca


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self.meta: list[tuple[str, str]] = []
        self.micro: list[tuple[str, str]] = []
        self.links: list[str] = []
        self.text: list[str] = []
        self._ld = False
        self._skip = 0
        self._itemprop: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "script":
            if "ld+json" in a.get("type", "").lower():
                self._ld = True
            else:
                self._skip += 1
        elif tag == "style":
            self._skip += 1
        elif tag == "meta":
            key = a.get("property") or a.get("name") or a.get("itemprop") or ""
            if key and a.get("content"):
                (self.micro if a.get("itemprop") else self.meta).append((key.lower(), a["content"]))
        elif tag == "link" and a.get("rel", "").lower() == "canonical" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        ip = a.get("itemprop")
        if ip and tag != "meta":
            if a.get("content"):
                self.micro.append((ip.lower(), a["content"]))
            elif a.get("href") and tag == "link":
                self.micro.append((ip.lower(), a["href"]))
            else:
                self._itemprop = ip.lower()

    def handle_endtag(self, tag):
        if tag == "script":
            if self._ld:
                self._ld = False
            elif self._skip:
                self._skip -= 1
        elif tag == "style" and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._ld:
            self.jsonld.append(data)
        elif not self._skip:
            if self._itemprop and data.strip():
                self.micro.append((self._itemprop, data.strip()))
                self._itemprop = None
            self.text.append(data)


def parse_html(raw: str) -> _Page:
    p = _Page()
    try:
        p.feed(raw)
        p.close()
    except Exception:  # noqa: BLE001 - niezaufany HTML
        pass
    return p


_GTIN_KEYS = ("gtin", "gtin8", "gtin12", "gtin13", "gtin14", "ean", "upc", "isbn")
_SKU_KEYS = ("sku", "mpn", "product:retailer_item_id")
_LABEL_GTIN = re.compile(r"(?i)\b(?:GTIN(?:-?(?:8|12|13|14))?|EAN(?:-?13)?|UPC|kod\s+kreskowy)\b\s*[:#=]?\s*(\d[\d \-]{6,17}\d)")
_LABEL_SKU = re.compile(r"(?i)\b(?:SKU|kod\s+produktu|nr\s+katalogowy|indeks|art\.?|numer\s+artyku[łl]u)\s*[:#=]\s*([A-Za-z0-9][A-Za-z0-9._/\-]{2,39})")
_LABEL_OFFER = re.compile(r"(?i)\b(?:numer\s+oferty|nr\s+oferty|id\s+oferty|offer\s*(?:id|number)|item\s*(?:id|number))\s*[:#=]?\s*([A-Za-z0-9]{5,20})")
_LABEL_SELLER = re.compile(r"(?i)\b(?:sprzedawca|sprzedaje|sold\s+by|seller|wystawca)\s*[:#=]\s*([^\n\r<|;,]{2,60}?)(?=\s+(?:EAN|GTIN|UPC|SKU|numer|nr|id|cena|kod|art)\b|[\n\r<|;,]|$)")


def _walk(node: Any) -> Iterable[dict]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _s(v: Any) -> Optional[str]:
    if isinstance(v, (str, int)) and not isinstance(v, bool) and str(v).strip():
        return str(v).strip()
    return None


def _seller_name(v: Any) -> Optional[str]:
    if isinstance(v, dict):
        return _s(v.get("name")) or _s(v.get("@id"))
    return _s(v)


def market_of(url: str, cfg: dict) -> Optional[str]:
    host = (urlparse(url).hostname or "").lower()
    for name, m in cfg["marketplaces"].items():
        if any(host == h or host.endswith("." + h) for h in m["hosts"]):
            return name
    return None


def url_ids(url: str, cfg: dict) -> list[Hit]:
    mk = market_of(url, cfg)
    if not mk:
        return []
    m = cfg["marketplaces"][mk]
    out: list[Hit] = []
    mo = re.search(m["offer"], url)
    if mo:
        out.append(Hit("offer_id", next(g for g in mo.groups() if g), "url", url, mk))
    ms = re.search(m["seller"], url)
    if ms:
        out.append(Hit("seller_id", ms.group(1), "url", url, mk))
    return out


def _jsonld(blobs: list[str], cfg: dict) -> list[Hit]:
    out: list[Hit] = []
    for blob in blobs:
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        for n in _walk(data):
            seller = _seller_name(n.get("seller")) if "seller" in n else None
            if seller:
                out.append(Hit("seller_id", seller, "jsonld", seller))
            for k in _GTIN_KEYS:
                if _s(n.get(k)):
                    out.append(Hit("gtin", _s(n[k]), "jsonld", _s(n[k])))
            for k in ("sku", "mpn"):
                if _s(n.get(k)):
                    out.append(Hit("sku", _s(n[k]), "jsonld", _s(n[k]), seller=seller))
            for k in ("url", "@id"):
                if _s(n.get(k)) and str(n[k]).startswith("http") and str(n.get("@type", "")).lower().endswith("offer"):
                    out.extend(url_ids(str(n[k]), cfg))
    return out


def scan_html(raw: str, source_url: str, cfg: dict) -> list[Hit]:
    page = parse_html(raw)
    hits = _jsonld(page.jsonld, cfg)
    for k, v in page.micro + page.meta:
        src = "microdata" if (k, v) in page.micro else "meta"
        if k in _GTIN_KEYS or k.replace("product:", "") in _GTIN_KEYS:
            hits.append(Hit("gtin", v, src, v))
        elif k == "sku" or k == "product:retailer_item_id":
            hits.append(Hit("sku", v, src, v))
        elif k == "seller":
            hits.append(Hit("seller_id", v, src, v))
    for u in [source_url, *page.links]:
        if u.startswith("http"):
            hits.extend(url_ids(u, cfg))
    hits.extend(scan_text(" ".join(page.text), "label"))
    return hits


def scan_text(text: str, source: str) -> list[Hit]:
    out = [Hit("gtin", m.group(1).strip(), source, m.group(1)) for m in _LABEL_GTIN.finditer(text)]
    seller = None
    for m in _LABEL_SELLER.finditer(text):
        seller = m.group(1).strip()
        out.append(Hit("seller_id", seller, source, seller))
    out += [Hit("sku", m.group(1), source, m.group(1), seller=seller) for m in _LABEL_SKU.finditer(text)]
    out += [Hit("offer_id", m.group(1), source, m.group(1)) for m in _LABEL_OFFER.finditer(text)]
    return out
