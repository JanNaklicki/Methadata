"""Komparatory: podobne zdania z OCR, układ reklamy, zrzut vs HTML; wpływ błędów OCR."""
from __future__ import annotations

import random
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, Feature, PairEvidence, Relation)
from methadata.contracts.models import ImageRect, TextSpan

from .layout import analyze_layout, layout_similarity, sentences
from .match import match_to_html
from .model import OcrLine, OcrResult, Rect, norm, union

FAMILY = "ocr"
KIND_BOXES = "ocr_boxes"
MIN_CONF = 0.5          # linie o niższej pewności są pomijane (raportowane w wyjaśnieniu)
SENT_RATIO = 0.8        # próg podobieństwa zdań (znormalizowanych)
MIN_SENT_CHARS = 8      # krótsze zdania (np. pojedyncze słowa) nie są dowodem


def image_ref(artifact_id: str, res: OcrResult, rect: Rect) -> ArtifactRef:
    x0, y0 = max(0, int(rect[0])), max(0, int(rect[1]))
    x1, y1 = min(res.width, int(rect[0] + rect[2] + 0.999)), min(res.height, int(rect[1] + rect[3] + 0.999))
    return ArtifactRef(artifact_id=artifact_id, locator=ImageRect(x=x0, y=y0, width=max(1, x1 - x0), height=max(1, y1 - y0)))


def ocr_results(features: Iterable[Feature]) -> list[tuple[str, OcrResult]]:
    """[(artifact_id, OcrResult)] z cech `ocr/ocr_boxes` — wspólne wejście dla adapterów S i komparatorów."""
    out = []
    for f in features:
        if f.family == FAMILY and f.kind == KIND_BOXES and isinstance(f.value, dict) and f.evidence_refs:
            out.append((f.evidence_refs[0].artifact_id, OcrResult.from_dict(f.value)))
    return out


def match_sentences(a: OcrResult, b: OcrResult, ratio: float = SENT_RATIO) -> dict:
    sa = [s for s in sentences(a) if len(norm(s.text)) >= MIN_SENT_CHARS]
    sb = [s for s in sentences(b) if len(norm(s.text)) >= MIN_SENT_CHARS]
    na, nb = [norm(s.text) for s in sa], [norm(s.text) for s in sb]
    cand = []
    for i, x in enumerate(na):
        for j, y in enumerate(nb):
            if min(len(x), len(y)) / max(len(x), len(y)) < ratio - 0.2:
                continue
            r = 1.0 if x == y else SequenceMatcher(None, x, y, autojunk=False).ratio()
            if r >= ratio:
                cand.append((r, i, j))
    cand.sort(key=lambda t: (-t[0], t[1], t[2]))
    ua, ub, pairs = set(), set(), []
    for r, i, j in cand:
        if i not in ua and j not in ub:
            ua.add(i)
            ub.add(j)
            pairs.append((sa[i], sb[j], r))
    total = sum(len(x) for x in na) + sum(len(y) for y in nb)
    matched = sum(r * (len(norm(p.text)) + len(norm(q.text))) for p, q, r in pairs)
    return {"pairs": pairs, "score": matched / total if total else None,
            "exact": sum(1 for _, _, r in pairs if r == 1.0), "n_a": len(sa), "n_b": len(sb)}


class _OcrBase:
    method_id = ""
    relation = Relation.shared_narrative

    def __init__(self, min_conf: float = MIN_CONF):
        self.min_conf = min_conf

    def _sorted(self, a, b, features):
        return sorted([(a.canonical_id, features.get(a.observation_id, [])),
                       (b.canonical_id, features.get(b.observation_id, []))], key=lambda t: t[0])

    def _none(self, pair, why):
        return [PairEvidence(run_id=self._run, pair=pair, method_id=self.method_id, relation=self.relation,
                             score=None, explanation=why)]

    def _inputs(self, fa, fb):
        ra = [(aid, r.filtered(self.min_conf)) for aid, r in ocr_results(fa)]
        rb = [(aid, r.filtered(self.min_conf)) for aid, r in ocr_results(fb)]
        return [x for x in ra if x[1].lines], [x for x in rb if x[1].lines]

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        self._run = run_id
        (ca, fa), (cb, fb) = self._sorted(a, b, features)
        pair = (ca, cb)
        ra, rb = self._inputs(fa, fb)
        if not ra or not rb:
            return self._none(pair, "brak podstaw: pusty lub brakujący wynik OCR po co najmniej jednej stronie "
                                    f"(próg pewności {self.min_conf})")
        best = None
        for aid, x in ra:
            for bid, y in rb:
                r = self._score(aid, x, bid, y)
                if r is not None and (best is None or r[0] > best[0]):
                    best = r
        if best is None:
            return self._none(pair, "brak podstaw: " + self.why_none)
        score, refs_a, refs_b, expl = best
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=self.relation,
                             score=min(1.0, max(0.0, score)), evidence_refs={ca: refs_a, cb: refs_b}, explanation=expl)]


class OcrSentenceComparator(_OcrBase):
    method_id = "ocr-sentences-v1"
    why_none = "brak zdań wystarczającej długości do porównania"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Podobne zdania z OCR zrzutów/kreacji; dowód = prostokąty na obu obrazach")

    def _score(self, aid, x, bid, y):
        m = match_sentences(x, y)
        if m["score"] is None:
            return None
        pairs = m["pairs"]
        if pairs:
            ra = [image_ref(aid, x, p.rect) for p, _, _ in pairs]
            rb = [image_ref(bid, y, q.rect) for _, q, _ in pairs]
        else:  # wynik 0: dowodem jest cały obszar tekstu
            ra = [image_ref(aid, x, union(l.rect for l in x.lines))]
            rb = [image_ref(bid, y, union(l.rect for l in y.lines))]
        cf = (x.mean_conf + y.mean_conf) / 2
        exp = (f"Podobne zdania z OCR ({x.engine} {x.engine_version}): {len(pairs)} par (w tym {m['exact']} identycznych "
               f"po normalizacji, reszta rozmyta >= {SENT_RATIO}) z {m['n_a']}/{m['n_b']} zdań; wynik {m['score']:.4f}; "
               f"średnia pewność OCR {cf:.2f}. Cytat: " +
               ("; ".join(f"\"{p.text}\" ~ \"{q.text}\" ({r:.2f})" for p, q, r in pairs[:3]) or "brak wspólnych zdań"))
        return m["score"], ra, rb, exp


class OcrLayoutComparator(_OcrBase):
    method_id = "ocr-layout-v1"
    relation = Relation.shared_template
    why_none = "nie rozpoznano układu (potrzebne co najmniej dwie wspólne role: tytuł/ilustracja/przycisk)"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Układ tytuł–ilustracja–przycisk w koordynatach względnych (IoU)")

    def _score(self, aid, x, bid, y):
        la, lb = analyze_layout(x), analyze_layout(y)
        s = layout_similarity(la, lb)
        if s is None:
            return None
        roles = [r for r in ("title", "illustration", "button") if la.get(r) and lb.get(r)]

        def rect(res, lay, r):
            return res.px(tuple(lay[r]["rect_rel"]))
        ra = [image_ref(aid, x, rect(x, la, r)) for r in roles]
        rb = [image_ref(bid, y, rect(y, lb, r)) for r in roles]
        exp = (f"Układ w koordynatach względnych: role wspólne {', '.join(roles)}; średnia IoU {s:.4f}"
               f" (role obecne tylko po jednej stronie liczone jako 0). Rozdzielczości {x.width}x{x.height} vs "
               f"{y.width}x{y.height} nie wpływają na wynik.")
        return s, ra, rb, exp


def html_sections(features: Iterable[Feature]) -> list[dict]:
    out = []
    for f in features:
        if f.family in ("html", "dom") and f.kind == "text_section" and isinstance(f.value, dict) \
                and not f.value.get("hidden") and f.value.get("tag") != "title" and f.evidence_refs:
            d = dict(f.value)
            d["_artifact_id"] = f.evidence_refs[0].artifact_id
            out.append(d)
    return out


class OcrHtmlComparator(_OcrBase):
    """Zrzut jednej strony vs zapisany HTML drugiej (lub tej samej obserwacji gdy a == b nie występuje w parach)."""
    method_id = "ocr-html-match-v1"
    why_none = "brak sekcji tekstowych HTML do dopasowania"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Dopasowanie zdań z OCR zrzutu do tekstu zapisanego HTML; dowód: prostokąt + TextSpan")

    def compare(self, a, b, features, *, run_id, optional=None):
        self._run = run_id
        (ca, fa), (cb, fb) = self._sorted(a, b, features)
        pair = (ca, cb)
        best = None
        for side, (ocr_c, ocr_f, html_c, html_f) in enumerate([(ca, fa, cb, fb), (cb, fb, ca, fa)]):
            sec = html_sections(html_f)
            for aid, res in ocr_results(ocr_f):
                res = res.filtered(self.min_conf)
                if not res.lines or not sec:
                    continue
                m = match_to_html(res, sec)
                if m["ocr_coverage"] is None or not m["matches"]:
                    cov = m["ocr_coverage"]
                    cand = (cov or 0.0, side, aid, res, m, sec)
                else:
                    cand = (m["ocr_coverage"], side, aid, res, m, sec)
                if best is None or cand[0] > best[0]:
                    best = cand
        if best is None:
            return self._none(pair, "brak podstaw: brak OCR zrzutu po jednej stronie lub brak tekstu HTML po drugiej")
        cov, side, aid, res, m, sec = best
        ocr_c, html_c = (ca, cb) if side == 0 else (cb, ca)
        if m["matches"]:
            r_img = [image_ref(aid, res, x.rect) for x in m["matches"]]
            r_html = []
            for x in m["matches"]:
                host = next((s["_artifact_id"] for s in sec if s["text"] == x.html_text), sec[0]["_artifact_id"])
                r_html.append(ArtifactRef(artifact_id=host, locator=TextSpan(start=x.html_span[0], end=x.html_span[1]))
                              if x.html_span and x.html_span[1] > x.html_span[0] else ArtifactRef(artifact_id=host))
        else:
            r_img = [image_ref(aid, res, union(l.rect for l in res.lines))]
            r_html = [ArtifactRef(artifact_id=sec[0]["_artifact_id"])]
        expl = (f"Zdania z OCR zrzutu ({res.engine}) znalezione w HTML: {cov:.2%} znaków OCR, "
                f"pokrycie tekstu HTML {m['html_coverage'] or 0:.2%}; {len(m['matches'])} dopasowanych zdań. Cytat: " +
                ("; ".join(f"\"{x.quote}\" -> \"{x.html_text[:60]}\"" for x in m["matches"][:3]) or "brak"))
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.duplicate_capture,
                             score=min(1.0, max(0.0, cov)), evidence_refs={ocr_c: r_img, html_c: r_html}, explanation=expl)]


_CONFUSE = {"l": "1", "1": "l", "o": "0", "0": "o", "i": "l", "e": "c", "m": "n", "u": "v", "a": "o", "s": "5"}


def perturb(res: OcrResult, rate: float, seed: int = 0) -> OcrResult:
    """Symulowane błędy OCR: podmiany znaków z tablicy pomyłek i wypadnięcia, z zadanym odsetkiem."""
    rng = random.Random(seed)
    lines = []
    for l in res.lines:
        out = []
        for ch in l.text:
            r = rng.random()
            if ch.isalnum() and r < rate:
                out.append(_CONFUSE.get(ch.lower(), "") if rng.random() < 0.7 else "")
            else:
                out.append(ch)
        lines.append(OcrLine("".join(out), l.rect, l.conf))
    return OcrResult(res.width, res.height, lines, res.engine, res.engine_version)


def error_impact(a: OcrResult, b: OcrResult, rates: Sequence[float] = (0.0, 0.02, 0.05, 0.1, 0.2), seed: int = 0) -> list[dict]:
    """Wpływ błędów OCR na wynik porównania zdań: [{rate, score, pairs, exact}] (średnia z 3 losowań)."""
    rows = []
    for rate in rates:
        sc, pr, ex, n = 0.0, 0, 0, 0
        for k in range(1 if rate == 0 else 3):
            m = match_sentences(perturb(a, rate, seed + k), perturb(b, rate, seed + 100 + k))
            sc += m["score"] or 0.0
            pr += len(m["pairs"])
            ex += m["exact"]
            n += 1
        rows.append({"rate": rate, "score": sc / n, "pairs": pr / n, "exact": ex / n})
    return rows
