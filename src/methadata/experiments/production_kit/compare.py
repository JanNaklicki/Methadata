"""Komparator E12: współwystępowanie kilku RZADKICH elementów zestawu (arkusz, klasy, skrypty lokalne, formularz).

Częstość df = liczba materiałów korpusu zawierających element. Element rzadki: df <= max(2, ceil(rare_frac * N)).
Para dostaje wynik tylko z >= min_shared wspólnych rzadkich elementów: W = Σ ln(N/df), score = 1 - exp(-W/ln N).
Wariant `nolib` odejmuje elementy z bibliotek i szablonu przeglądarki (df liczone nadal na całym korpusie)."""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import AdapterKind, AdapterSpec, PairEvidence, Relation

from .elements import Element, extract_elements

VERSION = "1"
METHOD_ALL = "production-kit-v1"
METHOD_NOLIB = "production-kit-nolib-v1"


class Corpus:
    """Częstości elementów w korpusie (df po obserwacjach) i próg rzadkości."""

    def __init__(self, n: int, df: Mapping[tuple, int], rare_frac: float = 0.2, min_shared: int = 2):
        self.n, self.df, self.rare_frac, self.min_shared = n, dict(df), rare_frac, min_shared

    @classmethod
    def fit(cls, observations, feature_map: Mapping[str, Sequence], **kw) -> "Corpus":
        df: Counter = Counter()
        for o in observations:
            df.update(extract_elements(o, feature_map.get(o.observation_id, [])).keys())
        return cls(len(list(observations)), df, **kw)

    def max_df(self) -> int:
        return max(2, math.ceil(self.rare_frac * self.n))

    def freq(self, key, both_sides: bool = True) -> int:
        return max(self.df.get(key, 0), 2 if both_sides else 1)


def shared_elements(ea: Mapping, eb: Mapping, corpus: Corpus, exclude_standard: bool) -> list[tuple[Element, Element, int]]:
    out = []
    for key in sorted(set(ea) & set(eb)):
        a, b = ea[key], eb[key]
        if exclude_standard and (a.origin != "own" or b.origin != "own"):
            continue
        df = corpus.freq(key)
        if df <= corpus.max_df():
            out.append((a, b, df))
    return out


def _evidence(ca, cb, ea, eb, corpus: Corpus, run_id, method, exclude_standard) -> PairEvidence:
    pair = (ca, cb)
    sh = shared_elements(ea, eb, corpus, exclude_standard)
    variant = "bez bibliotek i szablonu przeglądarki" if exclude_standard else "z bibliotekami"
    if len(sh) < corpus.min_shared:
        ra = [r for e in ea.values() for r in e.refs]
        rb = [r for e in eb.values() for r in e.refs]
        if not ra or not rb:
            return PairEvidence(run_id=run_id, pair=pair, method_id=method, relation=Relation.shared_template, score=None,
                                explanation="brak podstaw: elementy bez odwołań do artefaktów")
        return PairEvidence(run_id=run_id, pair=pair, method_id=method, relation=Relation.shared_template, score=0.0,
                            evidence_refs={ca: ra, cb: rb},
                            explanation=f"Wariant {variant}: {len(sh)} wspólnych rzadkich elementów (próg {corpus.min_shared}); "
                                        f"pojedynczy ślad nie jest współwystępowaniem (N={corpus.n}).")
    W = sum(math.log(corpus.n / df) for _, _, df in sh)
    score = min(1.0, 1 - math.exp(-W / math.log(max(corpus.n, 2))))
    ra = [r for a, _, _ in sh for r in a.refs]
    rb = [r for _, b, _ in sh for r in b.refs]
    parts = "; ".join(f"{a.kind} df={df}/{corpus.n} [{a.origin}] {a.label[:60]}" for a, _, df in sh)
    if not ra or not rb:
        return PairEvidence(run_id=run_id, pair=pair, method_id=method, relation=Relation.shared_template, score=None,
                            explanation="brak podstaw: elementy bez odwołań do artefaktów")
    return PairEvidence(run_id=run_id, pair=pair, method_id=method, relation=Relation.shared_template, score=score,
                        evidence_refs={ca: ra, cb: rb},
                        explanation=f"Wariant {variant}: {len(sh)} wspólnych rzadkich elementów, W=Σln(N/df)={W:.3f}: {parts}.")


class _Base:
    method_id = ""
    exclude_standard = False

    def __init__(self, corpus: Optional[Corpus] = None):
        self.corpus = corpus

    @classmethod
    def fit(cls, observations, feature_map, **kw):
        return cls(Corpus.fit(observations, feature_map, **kw))

    def compare(self, a, b, features, *, run_id, optional: Optional[Mapping[str, Any]] = None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        if self.corpus is None or self.corpus.n < 3:
            return [PairEvidence(run_id=run_id, pair=(ca, cb), method_id=self.method_id, relation=Relation.shared_template,
                                 score=None, explanation="brak podstaw: komparator wymaga korpusu >= 3 materiałów "
                                                         "(zbuduj przez fit(observations, feature_map))")]
        ea = extract_elements(oa, features.get(oa.observation_id, []))
        eb = extract_elements(ob, features.get(ob.observation_id, []))
        if not ea or not eb:
            return [PairEvidence(run_id=run_id, pair=(ca, cb), method_id=self.method_id, relation=Relation.shared_template,
                                 score=None, explanation="brak podstaw: jedna ze stron nie ma elementów zestawu (arkusz/klasa/skrypt/formularz)")]
        return [_evidence(ca, cb, ea, eb, self.corpus, run_id, self.method_id, self.exclude_standard)]


class ProductionKitComparator(_Base):
    method_id = METHOD_ALL
    spec = AdapterSpec(id=METHOD_ALL, kind=AdapterKind.comparator, version=VERSION,
                       description="E12: współwystępowanie rzadkich elementów zestawu strony (z bibliotekami)")


class ProductionKitNoLibComparator(_Base):
    method_id = METHOD_NOLIB
    exclude_standard = True
    spec = AdapterSpec(id=METHOD_NOLIB, kind=AdapterKind.comparator, version=VERSION,
                       description="E12: j.w., po odjęciu gotowych bibliotek i elementów szablonu przeglądarki")
