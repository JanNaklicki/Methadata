"""Czyste algorytmy na sekwencjach struktury: ścieżki, Jaccard, odległość edycyjna, wspólna sekwencja."""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Optional, Sequence

TEXT = "#t"  # marker tekstu (treść zastąpiona znacznikiem)
LANDMARKS = ("header", "nav", "main", "article", "section", "aside", "footer", "form", "table", "ul", "ol", "figure",
             "h1", "h2", "h3")
SKIP_TAGS = {"script", "style", "noscript", "template", "head", "title", "meta", "link", "base"}


def drop_subtrees(tokens: Sequence[Sequence], drop: Sequence[str]) -> list[int]:
    """Indeksy tokenów [głębokość, tag, ...] po usunięciu poddrzew elementów o tagach z `drop`."""
    drop = set(drop)
    keep: list[int] = []
    skip_depth: Optional[int] = None
    for i, t in enumerate(tokens):
        d, tag = t[0], t[1]
        if skip_depth is not None:
            if d > skip_depth:
                continue
            skip_depth = None
        if tag in drop:
            skip_depth = d
            continue
        keep.append(i)
    return keep


def tag_paths(tokens: Sequence[Sequence], idx: Sequence[int]) -> list[str]:
    """Ścieżka tagów (html>body>div>p) dla każdego z wybranych tokenów, w kolejności dokumentu."""
    stack: list[str] = []
    out: list[str] = []
    for i in idx:
        d, tag = tokens[i][0], tokens[i][1]
        del stack[d:]
        while len(stack) < d:
            stack.append("?")  # brakujący przodek (po usunięciu/niedomkniętym HTML)
        stack.append(tag)
        out.append(">".join(stack))
    return out


def jaccard(a, b) -> Optional[float]:
    a, b = set(a), set(b)
    u = a | b
    return len(a & b) / len(u) if u else None


def levenshtein(a: Sequence[str], b: Sequence[str]) -> int:
    """Odległość edycyjna (wstaw/usuń/zamień = 1), bitowo równoległa (Hyyrö) na liczbach całkowitych Pythona."""
    a, b = list(a), list(b)
    while a and b and a[0] == b[0]:
        a, b = a[1:], b[1:]
    while a and b and a[-1] == b[-1]:
        a, b = a[:-1], b[:-1]
    if not a or not b:
        return len(a) + len(b)
    if len(a) < len(b):
        a, b = b, a
    m = len(a)
    mask = (1 << m) - 1
    peq: dict[str, int] = {}
    for i, c in enumerate(a):
        peq[c] = peq.get(c, 0) | (1 << i)
    pv, mv, score, top = mask, 0, m, 1 << (m - 1)
    for c in b:
        eq = peq.get(c, 0)
        xv = eq | mv
        xh = ((((eq & pv) + pv) & mask) ^ pv) | eq
        ph = (mv | ~(xh | pv)) & mask
        mh = pv & xh
        if ph & top:
            score += 1
        elif mh & top:
            score -= 1
        ph = ((ph << 1) | 1) & mask
        mh = (mh << 1) & mask
        pv = (mh | ~(xv | ph)) & mask
        mv = ph & xv
    return score


def edit_similarity(a: Sequence[str], b: Sequence[str]) -> Optional[float]:
    n = max(len(a), len(b))
    return None if n == 0 else 1.0 - levenshtein(a, b) / n


def common_blocks(a: Sequence[str], b: Sequence[str]) -> list[tuple[int, int, int]]:
    """Wspólne bloki (i, j, długość) z SequenceMatcher; łącznie dają wspólny podciąg struktury."""
    sm = SequenceMatcher(None, list(a), list(b), autojunk=False)
    return [(m.a, m.b, m.size) for m in sm.get_matching_blocks() if m.size]
