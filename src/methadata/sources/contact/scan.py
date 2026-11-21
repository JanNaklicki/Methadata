"""S03: wydobycie kontaktów z tekstu (HTML po usunięciu skryptów/stylów, OCR). Treść jest niezaufana: tylko parsowanie."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

from . import normalize as N

_DEFAULT_CFG = Path(__file__).resolve().parents[4] / "configs" / "contact" / "contact.json"
_SKIP = {"script", "style", "noscript", "template", "svg"}
_BLOCK = {"p", "div", "li", "br", "tr", "td", "section", "footer", "header", "h1", "h2", "h3", "h4", "ul", "nav", "address", "a", "button"}
_HOSTS = r"(?:wa\.me|api\.whatsapp\.com|t\.me|telegram\.me|m\.me|messenger\.com|signal\.me|line\.me|invite\.viber\.com)"
_URL_RE = re.compile(r"(?:https?://)?(?:www\.)?" + _HOSTS + r"/[^\s\"'<>)]+", re.I)
_TG_RE = re.compile(r"\b(telegram|tg)\s*[:\-]?\s*@([A-Za-z][A-Za-z0-9_]{3,31})\b", re.I)
_PLAT_WORD = re.compile(r"(whatsapp|whats app|viber|signal|telegram|messenger)\W{0,6}$", re.I)
_PHONE_RE = re.compile(r"(?<![\w+/.-])\+?[\dOoIl(][\dOoIl () .\-]{6,22}[\dOoIl]")
_PHONE_HINT = re.compile(r"(tel|telefon|zadzwo|dzwo|call|phone|fon|kom\.|gsm|mobile|whatsapp|viber|infolini|hotline)", re.I)


def load_config(path: Optional[Path] = None) -> dict[str, Any]:
    p = Path(path) if path else _DEFAULT_CFG
    return json.loads(p.read_text(encoding="utf-8"))


@dataclass
class Hit:
    kind: str                 # phone | email | messenger
    key: str                  # znormalizowana wartość (nie opuszcza ekstraktora)
    masked: str
    role: str
    raw: str                  # fragment źródłowy do lokalizacji dowodu (nie zapisywany w cechach)
    source: str               # html | ocr
    country: str = ""
    platform: str = ""
    low_confidence: bool = False
    free_mail: bool = False


@dataclass
class Page:
    segments: list[tuple[str, str]] = field(default_factory=list)   # (tekst, kontekst tagów)
    links: list[tuple[str, str, str, str]] = field(default_factory=list)  # (href, tekst linku, kontekst)
    brand: Optional[str] = None


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.page = Page()
        self.stack: list[tuple[str, str]] = []
        self.skip = 0
        self._title = False
        self._a: Optional[list] = None

    def _ctx(self) -> str:
        return " ".join(c for _, c in self.stack[-6:])

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in _SKIP:
            self.skip += 1
            return
        if tag == "meta" and a.get("property", "").lower() == "og:site_name" and a.get("content"):
            self.page.brand = a["content"].strip()
        if tag == "title":
            self._title = True
        if tag in ("br",):
            self.page.segments.append(("\n", ""))
            return
        self.stack.append((tag, f"{tag} {a.get('class', '')} {a.get('id', '')}".lower()))
        if tag == "a" and a.get("href"):
            self._a = [a["href"], "", self._ctx(), "".join(t for t, _ in self.page.segments[-6:])[-45:]]
        if tag in _BLOCK:
            self.page.segments.append(("\n", ""))

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "title":
            self._title = False
        if tag == "a" and self._a:
            self.page.links.append((self._a[0], self._a[1].strip(), self._a[2], self._a[3]))
            self._a = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in _BLOCK:
            self.page.segments.append(("\n", ""))

    def handle_data(self, data):
        if self.skip or not data.strip():
            return
        if self._title and not self.page.brand:
            t = re.split(r"\s[|\-–—•]\s", data.strip())[-1] if re.search(r"\s[|\-–—•]\s", data) else data.strip()
            self.page.brand = t.strip()
        if self._a is not None:
            self._a[1] += data
        self.page.segments.append((data, self._ctx()))


def parse_html(raw: str) -> Page:
    p = _Parser()
    try:
        p.feed(raw)
        p.close()
    except Exception:  # noqa: BLE001 - uszkodzony HTML to nadal wejście; bierzemy, co się sparsowało
        pass
    if p._a:
        p.page.links.append((p._a[0], p._a[1].strip(), p._a[2], p._a[3]))
    return p.page


def role_for(window: Any, cfg: dict[str, Any], local: str = "") -> str:
    """window = (przed, po, kontekst tagów). Wygrywa słowo kluczowe najbliżej wartości (ta sama linia, potem poprzednia)."""
    if isinstance(window, str):
        window = (window, "", "")
    pre, post, ctx = window
    if local:
        if local in cfg["email_order_locals"]:
            return "order"
        if local in cfg["email_support_locals"]:
            return "support"
    groups = (("order", "order_keywords"), ("hotline", "hotline_keywords"), ("support", "support_keywords"))

    def nearest(chunk: str) -> Optional[str]:
        c = chunk.lower()
        best, pos = None, -1
        for role, key in groups:
            for k in cfg[key]:
                i = c.rfind(k)
                if i > pos or (i == pos and i >= 0 and len(k) > 0 and best is None):
                    best, pos = role, i
        return best

    lines = pre.split("\n")
    for chunk in (lines[-1], lines[-2] if len(lines) > 1 else "", post.split("\n")[0][:20], ctx):
        r = nearest(chunk)
        if r:
            return r
    return "general"


def scan(page_or_text: Any, cfg: dict[str, Any], source: str) -> list[Hit]:
    """Zwraca trafienia z HTML (Page) lub tekstu OCR (str). Duplikaty (kind,key) łączone z najsilniejszą rolą."""
    region = cfg["default_region"]
    ocr = source == "ocr"
    if isinstance(page_or_text, str):
        segs = [(page_or_text, "")]
        links: list = []
    else:
        segs, links = page_or_text.segments, page_or_text.links
    text = ""
    ctxs: list[tuple[int, int, str]] = []
    for s, c in segs:
        s = N.deobfuscate(s)
        ctxs.append((len(text), len(text) + len(s), c))
        text += s
    text = text.replace(" ", " ")

    def ctx_at(pos: int) -> str:
        for a, b, c in ctxs:
            if a <= pos < b:
                return c
        return ""

    def window(pos: int, end: int):
        return text[max(0, pos - 45):pos], text[end:end + 40], ctx_at(pos)

    hits: dict[tuple, Hit] = {}
    prio = {"order": 3, "hotline": 2, "support": 1, "general": 0}

    def put(h: Hit):
        k = (h.kind, h.key)
        old = hits.get(k)
        if old is None or prio[h.role] > prio[old.role] or (old.source == "ocr" and h.source == "html"):
            hits[k] = h

    def phone_hit(raw: str, win: str, explicit: bool):
        r = N.normalize_phone(raw, region, ocr=ocr, explicit_context=explicit)
        if r:
            e164, iso, fixed = r
            put(Hit("phone", e164, N.mask_phone(e164), role_for(win, cfg), raw, source, country=iso, low_confidence=fixed))

    def msg_hit(platform: str, ident: str, raw: str, win: str):
        put(Hit("messenger", f"{platform}:{ident}", N.mask_account(platform, ident), role_for(win, cfg), raw, source,
                platform=platform, country=""))

    def email_hit(raw: str, win: str):
        r = N.normalize_email(N.deobfuscate(raw))
        if r:
            addr, local, dom = r
            if local in ("noreply", "no-reply", "donotreply"):
                return
            put(Hit("email", addr, N.mask_email(local, dom), role_for(win, cfg, local), raw, source,
                    free_mail=dom in cfg["free_mail_domains"]))

    # odnośniki (tel:, mailto:, komunikatory) -- najsilniejszy sygnał CTA
    for href, label, c, before in links:
        h = href.strip()
        win = (before, label, c)
        if h.lower().startswith("tel:"):
            phone_hit(h[4:].replace("%20", " "), win, True)
        elif h.lower().startswith("mailto:"):
            email_hit(h, win)
        else:
            m = N.parse_messenger_url(h, region)
            if m:
                msg_hit(m[0], m[1], h, win)
    scan_text = text
    for m in _URL_RE.finditer(scan_text):
        r = N.parse_messenger_url(m.group(0).rstrip(".,;"), region)
        if r:
            msg_hit(r[0], r[1], m.group(0), window(m.start(), m.end()))
    for m in _TG_RE.finditer(scan_text):
        msg_hit("telegram", m.group(2).lower(), m.group(0), window(m.start(), m.end()))
    for m in EMAIL_ITER(scan_text):
        email_hit(m.group(0), window(m.start(), m.end()))
    for m in _PHONE_RE.finditer(scan_text):
        cand = m.group(0)
        pre = scan_text[max(0, m.start() - 14):m.start()].lower()
        if any(n in pre for n in cfg["negative_context"]):
            continue
        toks = cand.split()
        win = window(m.start(), m.end())
        explicit = bool(_PHONE_HINT.search(scan_text[max(0, m.start() - 30):m.start()]))
        found = False
        for i in range(len(toks)):
            for j in range(len(toks), i, -1):
                sub = " ".join(toks[i:j]).strip(".-")
                if sum(c.isdigit() or c in "OoIl" for c in sub) < 6 or sum(c.isdigit() for c in sub) < (3 if ocr else 6):
                    continue
                r = N.normalize_phone(sub, region, ocr=ocr, explicit_context=explicit)
                if not r:
                    continue
                pw = _PLAT_WORD.search(scan_text[max(0, m.start() - 24):m.start() + sum(len(t) + 1 for t in toks[:i])])
                if pw:
                    msg_hit(pw.group(1).lower().replace(" ", ""), r[0], sub, win)
                else:
                    phone_hit(sub, win, explicit)
                found = True
                break
            if found:
                break
    return list(hits.values())


def EMAIL_ITER(text: str):
    return N.EMAIL_RE.finditer(text)
