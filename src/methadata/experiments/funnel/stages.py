"""Wykrywanie etapów lejka (artykuł -> wezwanie -> formularz) w dokumencie HTML. Statyczna analiza: nic nie jest
wykonywane ani pobierane. Dla każdego rodzaju etapu zwracane jest jedno wystąpienie (pierwsze; wezwanie: pierwsze nie wcześniej niż artykuł)."""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from typing import Iterator, Optional

from methadata.extractors.html.parse import Node, norm_ws, parse_html

ARTICLE, CTA, FORM = "article", "cta", "form"
REDIRECT, SUBMIT = "redirect", "submit"
KINDS = (ARTICLE, CTA, FORM, REDIRECT, SUBMIT)

_CTA_RE = re.compile(
    r"(?<!\w)(kliknij|sprawdź|sprawdz|zarejestruj|zapisz się|zapisz sie|dołącz|dolacz|zamów|zamow|odbierz|kup\b|zacznij|"
    r"załóż|zaloz|wypełnij|wypelnij|rozpocznij|pobierz|skorzystaj|złóż|zloz|click|register|sign up|join|order|claim|"
    r"get started|buy|start now|apply|download|subscribe)", re.I)
_SKIP_INPUT = {"hidden", "submit", "button", "reset", "image", "search"}
_MAX_CTA_TEXT = 80
_ARTICLE_MIN_CHARS = 200
_PARA_MIN_CHARS = 400


@dataclass
class Stage:
    kind: str
    start: int   # znaki dekodowanego tekstu HTML
    end: int
    detail: str


def _walk(n: Node) -> Iterator[Node]:
    for c in n.children:
        yield c
        yield from _walk(c)


_TAG_RE = re.compile(r"<(script|style)\b.*?</\1\s*>|<[^>]*>", re.I | re.S)


def text_of(n: Node, src: str) -> str:
    """Tekst widoczny elementu: źródło między znacznikami bez tagów (tekst elementów inline nie trafia do runs)."""
    return norm_ws(_html.unescape(_TAG_RE.sub(" ", src[n.open_end:max(n.end, n.open_end)])))


def _inside(n: Node, tag: str) -> bool:
    p = n.parent
    while p is not None:
        if p.tag == tag:
            return True
        p = p.parent
    return False


def _span(n: Node) -> tuple[int, int]:
    return n.open_start, max(n.end, n.open_start + 1)


def analyze_html(text: str) -> list[Stage]:
    """Etapy w kolejności dokumentu: article, cta, form (każdy najwyżej raz)."""
    doc = parse_html(text)
    nodes = [n for n in doc.nodes if not _inside_skip(n)]
    found: dict[str, Stage] = {}
    ctas: list[Stage] = []
    for n in nodes:
        if n.hidden:
            continue
        if n.tag == "article" and ARTICLE not in found:
            t = text_of(n, text)
            if len(t) >= _ARTICLE_MIN_CHARS:
                found[ARTICLE] = Stage(ARTICLE, *_span(n), f"element <article>, {len(t)} znaków tekstu")
        elif n.tag == "form" and FORM not in found:
            fields = []
            for d in _walk(n):
                if d.tag in ("select", "textarea"):
                    fields.append(d.tag)
                elif d.tag == "input":
                    ty = (d.attrs.get("type") or "text").lower()
                    if ty not in _SKIP_INPUT:
                        fields.append(ty)
            if fields and (n.attrs.get("role") or "").lower() != "search":
                found[FORM] = Stage(FORM, *_span(n), "formularz z polami: " + ",".join(sorted(set(fields))))
        elif n.tag in ("a", "button") and not _inside(n, "form") and not _inside(n, "button"):
            t = text_of(n, text)
            if t and _CTA_RE.search(t):
                ctas.append(Stage(CTA, *_span(n), f"wezwanie: <{n.tag}> „{t[:_MAX_CTA_TEXT]}”"))
    if ARTICLE not in found:
        ps = [n for n in nodes if n.tag == "p" and not n.hidden]
        texts = [text_of(p, text) for p in ps]
        if len(ps) >= 3 and sum(map(len, texts)) >= _PARA_MIN_CHARS:
            found[ARTICLE] = Stage(ARTICLE, ps[0].open_start, max(ps[-1].end, ps[0].open_start + 1),
                                   f"{len(ps)} akapitów, {sum(map(len, texts))} znaków tekstu")
    # wezwanie: pierwsze po początku artykułu (menu/nagłówek przed artykułem to zwykle nawigacja serwisu);
    # bez artykułu pierwsze w dokumencie
    after = [c for c in ctas if ARTICLE not in found or c.start >= found[ARTICLE].start]
    if after:
        found[CTA] = after[0]
    return sorted(found.values(), key=lambda s: (s.start, s.kind))


def _inside_skip(n: Node) -> bool:
    p: Optional[Node] = n
    while p is not None:
        if p.tag in ("script", "style", "noscript", "template", "head"):
            return True
        p = p.parent
    return False
