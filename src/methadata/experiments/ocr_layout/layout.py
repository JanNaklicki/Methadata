"""Układ tytuł–ilustracja–przycisk (koordynaty względne) i zdania z prostokątami."""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from .model import OcrResult, Rect, norm, union

CTA = ["dowiedz sie wiecej", "kup teraz", "zobacz wiecej", "zobacz", "sprawdz", "zamow", "zamow teraz", "zarejestruj sie",
       "zapisz sie", "pobierz", "wyprobuj", "skontaktuj sie", "wiecej", "learn more", "shop now", "sign up",
       "get started", "buy now", "download", "order now", "read more", "apply now", "zagraj", "wejdz", "klik"]
MIN_ILLUSTRATION_H = 0.08  # minimalna wysokość pasa ilustracji (część wysokości obrazu)


@dataclass
class Sentence:
    text: str
    rect: Rect          # piksele; suma prostokątów linii, które zdanie dotyka
    line_idx: tuple[int, ...]


def _blocks(res: OcrResult) -> list[list[int]]:
    """Linie sąsiednie w pionie o podobnej wysokości tworzą blok (akapit)."""
    order = sorted(range(len(res.lines)), key=lambda i: (res.lines[i].rect[1], res.lines[i].rect[0]))
    blocks: list[list[int]] = []
    for i in order:
        r = res.lines[i].rect
        if blocks:
            pr = res.lines[blocks[-1][-1]].rect
            gap = r[1] - (pr[1] + pr[3])
            ratio = max(r[3], pr[3]) / max(1.0, min(r[3], pr[3]))
            if gap < 0.8 * min(r[3], pr[3]) and ratio < 1.35 and abs(r[0] - pr[0]) < 0.25 * res.width:
                blocks[-1].append(i)
                continue
        blocks.append([i])
    return blocks


def sentences(res: OcrResult) -> list[Sentence]:
    out: list[Sentence] = []
    for blk in _blocks(res):
        text, owner = "", []
        for i in blk:
            t = res.lines[i].text.strip()
            if not t:
                continue
            if text:
                text += " "
                owner.append(i)
            text += t
            owner.extend([i] * len(t))
        pos = 0
        for m in re.finditer(r"[^.!?…]+(?:[.!?…]+|$)", text):
            seg = m.group(0)
            s = seg.strip()
            if not norm(s):
                continue
            a = m.start() + (len(seg) - len(seg.lstrip()))
            idx = sorted(set(owner[a:a + len(s)]))
            out.append(Sentence(s, union(res.lines[i].rect for i in idx), tuple(idx)))
            pos = m.end()
        del pos
    return out


def _is_cta(text: str) -> bool:
    n = norm(text)
    if not n or len(n.split()) > 4:
        return False
    return any(n == c or SequenceMatcher(None, n, c).ratio() >= 0.85 for c in CTA)


def analyze_layout(res: OcrResult) -> dict[str, Optional[dict]]:
    """Zwraca {'title','illustration','button'} -> {'rect_rel': [x,y,w,h], 'text'?} lub None. Heurystyka wersjonowana."""
    lines = [l for l in res.lines if norm(l.text)]
    out: dict[str, Optional[dict]] = {"title": None, "illustration": None, "button": None}
    if not lines:
        return out
    H, W = res.height, res.width
    buttons = [l for l in lines if _is_cta(l.text)]
    button = max(buttons, key=lambda l: l.rect[1]) if buttons else None
    cands = [l for l in lines if l is not button and l.rect[1] < 0.7 * H]
    title = None
    if cands:
        med = statistics.median(l.rect[3] for l in lines)
        big = max(cands, key=lambda l: (round(l.rect[3] / max(1.0, med), 1), -l.rect[1]))
        # tytuł = blok linii o wysokości zbliżonej do największej, sąsiadujących pionowo
        grp = [l for l in cands if l.rect[3] >= 0.8 * big.rect[3] and abs(l.rect[1] - big.rect[1]) < 4 * big.rect[3]]
        title_rect = union(l.rect for l in grp)
        title = {"rect_rel": res.rel(title_rect), "text": " ".join(l.text for l in sorted(grp, key=lambda l: l.rect[1]))}
        out["title"] = {"rect_rel": [round(v, 5) for v in title["rect_rel"]], "text": title["text"]}
        top = title_rect[1] + title_rect[3]
    else:
        top = 0.0
    if button is not None:
        out["button"] = {"rect_rel": [round(v, 5) for v in res.rel(button.rect)], "text": button.text}
        bottom = button.rect[1]
    else:
        bottom = float(H)
    # ilustracja = największy pas bez tekstu między tytułem a przyciskiem (lub krawędzią obrazu)
    spans = sorted((l.rect[1], l.rect[1] + l.rect[3]) for l in lines
                   if l is not button and l.rect[1] >= top - 1 and l.rect[1] + l.rect[3] <= bottom + 1
                   and (title is None or l.text not in title["text"]))
    best, cur = (0.0, 0.0), top
    for s, e in spans + [(bottom, bottom)]:
        if s - cur > best[1] - best[0]:
            best = (cur, s)
        cur = max(cur, e)
    if (best[1] - best[0]) / H >= MIN_ILLUSTRATION_H and (title is not None or button is not None):
        out["illustration"] = {"rect_rel": [0.0, round(best[0] / H, 5), 1.0, round((best[1] - best[0]) / H, 5)]}
    return out


def _iou(a, b) -> float:
    ax1, ay1, bx1, by1 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw, ih = min(ax1, bx1) - max(a[0], b[0]), min(ay1, by1) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / (a[2] * a[3] + b[2] * b[3] - inter)


def layout_similarity(a: dict, b: dict) -> Optional[float]:
    """Średnia IoU prostokątów względnych po rolach występujących w którymkolwiek układzie; None = brak podstaw.
    Wymaga co najmniej dwóch wspólnych ról (jedna rola to zbyt mało na 'układ')."""
    roles = [r for r in ("title", "illustration", "button") if a.get(r) or b.get(r)]
    common = [r for r in roles if a.get(r) and b.get(r)]
    if len(common) < 2:
        return None
    return sum(_iou(a[r]["rect_rel"], b[r]["rect_rel"]) if r in common else 0.0 for r in roles) / len(roles)
