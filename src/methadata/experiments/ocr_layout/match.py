"""Dopasowanie zrzutu do zapisanego HTML i lokalizacja cytatu na obrazie."""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional, Sequence

from .layout import sentences
from .model import OcrResult, Rect, norm, union


@dataclass
class Match:
    quote: str            # tekst z OCR
    rect: Rect            # piksele na zrzucie
    rect_rel: Rect
    html_text: str        # dopasowany fragment HTML
    html_span: Optional[tuple[int, int]]   # (start, end) w dekodowanym HTML, gdy znany
    ratio: float


def coverage(needle: str, hay: str) -> float:
    """Jaka część znaków `needle` (znormalizowanego) występuje w kolejności w `hay`."""
    if not needle or not hay:
        return 0.0
    if needle in hay:
        return 1.0
    sm = SequenceMatcher(None, needle, hay, autojunk=False)
    return sum(b.size for b in sm.get_matching_blocks()) / len(needle)


def locate_quote(res: OcrResult, quote: str, min_ratio: float = 0.8) -> Optional[tuple[Rect, float]]:
    """Prostokąt (piksele) obejmujący cytat w wyniku OCR: najlepsze ciągłe okno linii. None, gdy brak."""
    q = norm(quote)
    if not q:
        return None
    lines = [l for l in res.lines if norm(l.text)]
    best = None  # (klucz: liczba linii, nadmiar znaków, -pokrycie), i, j, pokrycie
    for i in range(len(lines)):
        acc = ""
        for j in range(i, min(len(lines), i + 8)):
            acc = (acc + " " + norm(lines[j].text)).strip()
            cov = coverage(q, acc)
            if cov >= min_ratio:
                key = (j - i, len(acc) - len(q) if len(acc) > len(q) else 0, -cov)
                if best is None or key < best[0]:
                    best = (key, i, j, cov)
                break  # dłuższe okna od tego samego początku tylko dodają szum
    if best is None:
        return None
    return union(l.rect for l in lines[best[1]:best[2] + 1]), best[3]


def match_to_html(res: OcrResult, sections: Sequence[dict], *, min_ratio: float = 0.8) -> dict:
    """sections: [{'text','start','end'}] (cechy text_section ekstraktora html; start/end opcjonalne).
    Zwraca {'matches', 'ocr_coverage', 'html_coverage'}: odsetek znaków OCR znalezionych w HTML i odwrotnie."""
    secs = [(norm(s["text"]), s) for s in sections if norm(s.get("text", ""))]
    matches: list[Match] = []
    tot = got = 0
    seen_html: set[int] = set()
    for sen in sentences(res):
        n = norm(sen.text)
        if len(n) < 3:
            continue
        tot += len(n)
        best = (0.0, -1)
        for k, (sn, _) in enumerate(secs):
            c = coverage(n, sn)
            if c > best[0]:
                best = (c, k)
        if best[1] >= 0 and best[0] >= min_ratio:
            got += len(n)
            seen_html.add(best[1])
            s = secs[best[1]][1]
            sp = (int(s["start"]), int(s["end"])) if s.get("start") is not None and s.get("end") is not None else None
            matches.append(Match(sen.text, sen.rect, res.rel(sen.rect), s["text"], sp, best[0]))
    html_chars = sum(len(sn) for sn, _ in secs)
    html_got = sum(len(secs[k][0]) for k in seen_html)
    return {"matches": matches, "ocr_coverage": got / tot if tot else None,
            "html_coverage": html_got / html_chars if html_chars else None, "ocr_chars": tot}
